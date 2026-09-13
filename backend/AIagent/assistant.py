"""智能助手：把模型、工具与人工审批装成一个可对话的 agent。

命中 approval.APPROVAL_REQUIRED_TOOLS 的工具调用会先「挂起」，把待办动作交出去
（Web 端推到前端让用户点批准/修改/拒绝，本地脚本自动批准），确认后才真正执行。

挂起与恢复靠 checkpointer 保存「图运行状态」（挂起中的工具调用、待办清单等），
所以每次对话必须带一个 thread_id：
  - Web 端：对话历史存在会话库（db.py 的 conversation_messages），每轮从库里
    重建完整上下文传给 agent；thread_id 由 message.py 每轮新生成，跑完即删。
    复用同一个 thread_id 会让 checkpointer 里的旧状态与新入参叠加，消息重复；
  - 本地脚本：chat() 也是每轮自己生成新 thread_id，历史由 self.messages 维护。
"""

import uuid

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    HumanInTheLoopMiddleware,
    LLMToolSelectorMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from rich import print as rprint

import config
from AIagent.approval import APPROVAL_REQUIRED_TOOLS, extract_approval_actions
from AIagent.file_tools import FILE_TOOLS
from AIagent.llm import build_fallback_model, model
from AIagent.middleware import (
    InputGuardMiddleware,
    ObservabilityMiddleware,
    build_pii_middlewares,
)
from AIagent.text import message_text
from AIagent.tools import ASSISTANT_TOOLS
from logging_setup import get_logger

log = get_logger("assistant")

SYSTEM_PROMPT = """你是一个多功能智能助手，可以帮助用户：

🌤 查询天气：使用 get_weather 工具
🔢 数学计算：使用 calculator 工具
⏰ 时间查询：使用 get_time_info 工具
💱 货币转换：使用 convert_currency 工具
🔍 信息搜索：使用 search_info 工具

代码助手（需要用户先在前端「打开文件夹」选定工作目录，之后所有路径都写相对路径）：
📂 看目录：list_files      📖 读文件：read_file      🔎 搜代码：search_code
✏️ 写文件：write_file（新建或整文件覆盖）
🔧 改代码：replace_in_file（精确替换某个片段，改代码优先用它）
🗑 删文件：delete_path

重要提示：
1. 仔细阅读用户问题，确定需要使用哪个工具
2. 如果需要多个工具，按顺序调用
3. 总是用友好、专业的语气回答
4. 如果工具返回了数据，要用通俗易懂的语言解释给用户
5. 如果无法完成任务，诚实地告诉用户原因
6. 改代码的流程：先用 list_files / search_code 定位，再 read_file 拿到原文，
   然后才动手；只改几行用 replace_in_file，只有新建文件或整体重写才用 write_file
7. 任务较复杂时，先用 write_todos 拆成步骤，并把每步进度及时更新进去
8. 工具返回以「失败：」开头时，说明这一步没成功，看原因调整后重试

请始终使用中文回答。"""


def build_middleware_stack() -> list:
    """装配生产级中间件栈。

    顺序规则（LangChain 1.x）：**列表里第一个是最外层**。
    最外层的先跑、最后收尾；最内层的紧贴真正的模型/工具执行。

    这里的分层思路是「从外到内、从粗到细」：
      1. 观测    —— 先架好日志，后面所有步骤都能被看到
      2. 护栏    —— 输入不合格立刻拒，不烧钱
      3. 上下文  —— 先便宜地清理旧工具结果，再贵地做摘要
      4. 模型韧性 —— 预算 → 降级 → 重试
      5. 工具韧性 —— 预算 → 审批 → 重试 → 错误兜底
      6. 规划与审批 —— 待办清单 + 人工审批，贴在执行前

    每一步都能用 config 里的开关关掉，方便单独排查是哪个中间件出的问题。
    """
    stack: list = []

    # ---- 1. 可观测性（最外层：整轮耗时、token 用量、工具审计）----
    stack.append(ObservabilityMiddleware())

    # ---- 2. 输入护栏：超长/空输入在这一步就被拒，不消耗模型 token ----
    if config.AGENT_INPUT_GUARD_ENABLED:
        stack.append(InputGuardMiddleware())

    # 2.1 输入侧敏感信息脱敏：别把用户的手机号/身份证顺手送进第三方 API。
    #     放在最前面几层：脱敏后的内容才会进入后面的摘要与模型调用。
    if config.AGENT_PII_ENABLED:
        stack.extend(build_pii_middlewares())

    # ---- 3. 上下文治理 ----
    # 3.1 先做便宜的：把「旧的工具返回」换成占位符（工具输出通常最占 token，
    #     读一次文件就能塞进几千字，但它们往往只在当时那一步有用）
    if config.AGENT_CONTEXT_EDITING_ENABLED:
        stack.append(
            ContextEditingMiddleware(
                edits=[
                    ClearToolUsesEdit(
                        trigger=config.AGENT_CONTEXT_CLEAR_TRIGGER_TOKENS,
                        # 保留最近 N 次工具结果：正在进行的多步任务不能被打断
                        keep=config.AGENT_CONTEXT_CLEAR_KEEP,
                        # 保留工具「入参」：write_file 的参数就是文件内容，
                        # 清掉的话模型会失去「我到底写了什么」这个事实，容易重复写入
                        clear_tool_inputs=False,
                    )
                ]
            )
        )

    # 3.2 再做贵的：把更早的历史压缩成一段摘要（会多花一次模型调用）
    if config.AGENT_SUMMARY_ENABLED:
        stack.append(
            SummarizationMiddleware(
                model=model,
                # 命中任一条件即触发（先到的赢）：token 数或消息条数。
                # demo 刻意取小值，聊几轮就能看到摘要生效。
                trigger=[
                    ("tokens", config.AGENT_SUMMARY_TRIGGER_TOKENS),
                    ("messages", config.AGENT_SUMMARY_TRIGGER_MESSAGES),
                ],
                # 尾部必须原样保留的消息条数：摘要只能吃旧的，不能吃掉当前的上下文
                keep=("messages", config.AGENT_SUMMARY_KEEP_MESSAGES),
                trim_tokens_to_summarize=config.AGENT_SUMMARY_TRIM_TOKENS,
                # 用中文摘要，否则摘要结果会把英文摘要塞回中文对话里
                summary_prompt=(
                    "请把下面的对话历史压缩成一段简洁的中文摘要，"
                    "保留：用户的目标与偏好、已经得出结论的事实、"
                    "已经做过的操作及其结果、尚未完成的待办。"
                    "不要编造，不要输出无关的客套话。\n\n"
                    "待摘要的消息：\n{messages}"
                ),
            )
        )

    # ---- 4. 模型层韧性 ----
    # 预算放最外：它统计的是「逻辑上的模型调用次数」，重试/降级不该偷偷多花预算。
    stack.append(
        ModelCallLimitMiddleware(
            run_limit=config.AGENT_MODEL_RUN_LIMIT,  # 单轮上限
            thread_limit=config.AGENT_MODEL_THREAD_LIMIT,  # 同一会话累计上限
            exit_behavior="end",  # 超限就让模型收尾，而不是直接报错
        )
    )
    # 降级在重试外面：主模型重试仍失败 → 换备用模型（只降一次，不再回主模型）
    fallback = build_fallback_model()
    if fallback is not None:
        stack.append(ModelFallbackMiddleware(fallback))
    stack.append(
        ModelRetryMiddleware(
            max_retries=config.AGENT_MODEL_MAX_RETRIES,
            initial_delay=config.AGENT_RETRY_INITIAL_DELAY,
            max_delay=config.AGENT_RETRY_MAX_DELAY,
            backoff_factor=config.AGENT_RETRY_BACKOFF_FACTOR,
            jitter=True,  # 加抖动，避免多个请求同时重试把对方再打挂
            on_failure="continue",  # 重试完还失败就交给上层（降级/报错），不静默吞掉
        )
    )

    # ---- 5. 工具层韧性 ----
    # 预算是「护栏」不是「执行」，所以放在人工审批之外。
    stack.append(
        ToolCallLimitMiddleware(
            run_limit=config.AGENT_TOOL_RUN_LIMIT,
            thread_limit=config.AGENT_TOOL_THREAD_LIMIT,
            exit_behavior="continue",  # 超限就拦下这些工具，让模型换个思路作答
        )
    )

    # ---- 6. 任务规划 ----
    stack.append(TodoListMiddleware())

    # ---- 7. 工具路由（可选：工具很多才值得开，代价是每次多一次模型调用）----
    if config.AGENT_TOOL_SELECTOR_ENABLED:
        stack.append(
            LLMToolSelectorMiddleware(
                model=model,
                max_tools=config.AGENT_TOOL_SELECTOR_MAX_TOOLS,
                # 这几个是「干活」的工具，一旦被筛掉 Agent 就废了，必须常驻
                always_include=[*APPROVAL_REQUIRED_TOOLS],
                on_parsing_failure="continue",  # 选不出来就当没开，别把请求打挂
            )
        )

    # ---- 8. 人工审批 ----
    # 放在重试之外：审批只做一次。放里面的话，工具失败重试会让用户被反复要求点确认。
    stack.append(HumanInTheLoopMiddleware(interrupt_on=APPROVAL_REQUIRED_TOOLS))
    stack.append(
        ToolRetryMiddleware(
            max_retries=config.AGENT_TOOL_MAX_RETRIES,
            initial_delay=config.AGENT_RETRY_INITIAL_DELAY,
            max_delay=config.AGENT_RETRY_MAX_DELAY,
            backoff_factor=config.AGENT_RETRY_BACKOFF_FACTOR,
            jitter=True,
            # 让模型看到失败原因并自己决定下一步，比直接把 500 抛给用户好
            on_failure="continue",
        )
    )
    # 最内层兜底：工具抛出的异常转成 status="error" 的 ToolMessage，模型据此自我修正
    stack.append(ToolErrorMiddleware(on_error=_tool_error_to_message))

    return stack


def _tool_error_to_message(exc: Exception) -> str:
    """把工具异常转成一段给「模型」看的错误说明（不是给用户看的）。

    只保留异常类型与消息：堆栈留给日志，塞进上下文等于白烧 token。
    返回字符串 = 转成 ToolMessage(status="error")；模型看到后可以自己改参数重试。
    """
    log.warning("tool error: %s", exc)
    return f"工具执行失败（{type(exc).__name__}）：{exc}。请根据原因调整参数后重试，或换一个工具。"


class SmartAssistant:
    """一个智能助手，集成了多种工具和人工审批。"""

    def __init__(self):
        # 初始化模型
        self.model = model
        # 工具 = 业务工具 + 工作目录内的代码读写工具
        self.tools = [*ASSISTANT_TOOLS, *FILE_TOOLS]
        # 只管「这一轮还没跑完」的运行状态：挂起中的工具调用、待办清单、
        # 单轮内的工具消息（这些都不进数据库——库里只存给用户看的正文）。
        # 跨轮历史在会话库（db.py），两者职责不同，不要混着看。
        # 注意这是「进程内」存储：Flask 多进程部署（如 gunicorn 多 worker）时，
        # 发起审批和提交审批可能落到不同进程，那就得换成 Redis/Postgres 版 checkpointer。
        self.checkpointer = InMemorySaver()

        self.agent = create_agent(
            model=self.model,
            tools=self.tools,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=self.checkpointer,
            middleware=build_middleware_stack(),
        )

        # 只给本地脚本（chat / reset）用的会话历史。
        # Web 端不要用它：那边的历史在会话库里，每轮从库重建上下文，
        # 共享这一份列表会让不同会话互相串味。
        self.messages = []

    # ---------------- 会话 / 审批辅助 ----------------

    @staticmethod
    def thread_config(thread_id: str) -> dict:
        """把 thread_id 包成 LangGraph 需要的 config。"""
        return {"configurable": {"thread_id": thread_id}}

    def pending_actions(self, thread_id: str) -> list | None:
        """查这个会话有没有等着审批的动作；没有则返回 None。

        stream_mode="messages" 只产出模型分片，中断信息不在流里，
        所以流跑完后要回来问一次图状态（中断挂在 after_model 节点上）。
        """
        state = self.agent.get_state(self.thread_config(thread_id))
        return extract_approval_actions(
            interrupt for task in state.tasks for interrupt in task.interrupts
        )

    def pending_todos(self, thread_id: str) -> list | None:
        """读这个会话最新的待办清单，没有则返回 None。

        清单由 TodoListMiddleware 写入 state 的 "todos" 键，元素形如
        {"content": "...", "status": "pending|in_progress|completed"}。
        和中断一样，stream_mode="messages" 不带它，要等流跑完再问一次。
        """
        state = self.agent.get_state(self.thread_config(thread_id))
        return state.values.get("todos") or None

    def release_thread(self, thread_id: str) -> None:
        """删掉会话检查点，避免 InMemorySaver 里的线程无限增长。"""
        try:
            self.checkpointer.delete_thread(thread_id)
        except Exception as exc:  # noqa: BLE001 - 清理失败不该影响正常响应
            rprint("[yellow]清理会话失败：[/yellow]", exc)

    # ---------------- 对话入口 ----------------

    def chat(self, user_input: str) -> str:
        """本地脚本用的对话接口：由它自己维护多轮上下文。

        本地脚本没人能点审批，所以遇到中断就自动全部批准。
        """
        # 添加用户消息（用 HumanMessage，和 agent 返回的消息类型保持一致）
        self.messages.append(HumanMessage(content=user_input))
        # 每轮都开全新线程：下面每次都把全量历史当入参发出去，
        # 复用同一个 thread_id 会让 checkpointer 里的旧状态与之叠加，消息重复。
        thread_id = uuid.uuid4().hex
        config = self.thread_config(thread_id)
        try:
            # 调用 agent：LangGraph 编译图的入参必须是 {"messages": [...]} 这种状态字典
            result = self.agent.invoke({"messages": self.messages}, config=config)
            # 自动批准直到图真正跑完（模型可能连续调好几轮工具）
            while True:
                actions = extract_approval_actions(result.get("__interrupt__") or ())
                if not actions:
                    break
                rprint(f"[yellow]本地脚本自动批准 {len(actions)} 个工具调用[/yellow]")
                result = self.agent.invoke(
                    Command(resume={"decisions": [{"type": "approve"} for _ in actions]}),
                    config=config,
                )
            # 更新消息历史（含工具调用产生的消息，下一轮要一起回传）
            self.messages = result["messages"]
        finally:
            self.release_thread(thread_id)
        # 返回最后一条「有正文」的 AI 消息
        for msg in reversed(self.messages):
            if isinstance(msg, AIMessage) and message_text(msg).strip():
                return message_text(msg)
        return "抱歉，我无法处理这个请求。"

    def stream(self, messages, thread_id: str):
        """Web 端流式对话（无状态）：开始一轮新对话。

        messages：LangChain 消息对象列表（由 message.py 的 _to_messages()
        把会话库里的完整多轮历史转换好），所以这里不读写 self.messages。

        产出 (消息分片, 元数据) 二元组：
          - 只有 stream_mode="messages" 才会逐 token 产出；
            默认的 "updates" 是按节点聚合的 dict（形如
            {"model": {"messages": [...]}}），拿它取 content 会一直是空。
          - 该模式不带出中断信息，调用方需要自己问一次 pending_actions()。
        """
        return self.agent.stream(
            {"messages": messages},
            config=self.thread_config(thread_id),
            stream_mode="messages",
        )

    def resume(self, thread_id: str, decisions: list):
        """带着人工审批结果，继续被挂起的对话。

        decisions 是「按位置对应」的列表：顺序必须和 pending_actions() 返回的
        动作列表一致、长度也必须相等，否则中间件会抛
        ValueError: Number of human decisions (x) does not match number of
        hanging tool calls (y)。
        """
        return self.agent.stream(
            Command(resume={"decisions": decisions}),
            config=self.thread_config(thread_id),
            stream_mode="messages",
        )

    def reset(self):
        """重置对话历史"""
        self.messages = []
