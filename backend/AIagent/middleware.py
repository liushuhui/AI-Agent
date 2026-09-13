"""自定义 Agent 中间件：内置中间件之外的「观测」与「护栏」。

LangChain 1.x 的内置中间件已经覆盖了摘要、上下文清理、重试、限额、
人工审批、待办清单等（装配见 assistant.py）。这里补两类内置件没有、生产上又必须有的：

1. `ObservabilityMiddleware`
   把 Agent 内部的黑盒打开：一轮对话的开始/结束、每次模型调用的消息数与
   token 用量、每次工具调用的名称与耗时，并且把「写磁盘」的工具调用单独
   按 WARNING 级别记一条审计日志（谁在什么时候改了哪个文件）。

2. `InputGuardMiddleware`
   输入护栏：单条用户消息过长直接拒绝。比让模型把几万字符烧成 token 划算得多，
   也能挡住「不小心把整个文件粘进输入框」这类误操作。

写法约定（LangChain 1.x）：
  中间件按列表顺序包装，**第一个是最外层**。
  - before_agent / after_agent：一轮对话的首尾各一次
  - before_model / after_model：每次模型调用前后各一次
  - wrap_tool_call：包住每次工具执行，可以拿到名称、参数、耗时
  钩子返回 None 表示「不改状态」。
"""

import time

from langchain.agents.middleware import AgentMiddleware, PIIMiddleware, ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

import config
from AIagent.text import message_text
from logging_setup import get_logger

log = get_logger("agent")

# 会改动磁盘的工具：审计日志要能和「读」类工具区分开
WRITE_TOOLS = {"write_file", "replace_in_file", "delete_path"}


def _last_usage(messages) -> dict:
    """从最后一条 AI 消息里取 token 用量（没有就返回空字典）。"""
    for message in reversed(messages or ()):
        usage = getattr(message, "usage_metadata", None)
        if usage:
            return dict(usage)
    return {}


class ObservabilityMiddleware(AgentMiddleware):
    """可观测性：结构化日志 + token 用量 + 工具审计。

    注意：一个实例会被多个并发请求共用，所以这里**不存任何实例状态**，
    所有数据都从入参 state / request 里取，天然线程安全。
    """

    name = "observability"

    def before_agent(self, state, runtime):
        log.info(
            "agent run start",
            extra={"messages": len(state.get("messages") or [])},
        )
        return None

    def after_agent(self, state, runtime):
        messages = state.get("messages") or []
        # token 用量已经在 after_model 里逐次记过了，这里只收口「这轮跑完没」
        log.info("agent run end", extra={"messages": len(messages)})
        return None

    def before_model(self, state, runtime):
        messages = state.get("messages") or []
        # 到这一步说明记录条数没被中间件拦掉，用 DEBUG 免得日志刷屏
        log.debug("model call", extra={"messages": len(messages)})
        return None

    def after_model(self, state, runtime):
        messages = state.get("messages") or []
        usage = _last_usage(messages)
        if usage:
            log.info(
                "model used",
                extra={
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
            )
        return None

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        name = (request.tool_call or {}).get("name", "unknown")
        args = (request.tool_call or {}).get("args") or {}
        started = time.perf_counter()
        try:
            result = handler(request)
        except GraphBubbleUp:
            # interrupt() 之类的控制流信号：不是错误，是「挂起等人工审批」，
            # 必须原样抛出交给上层，这里只留一条日志。
            log.info("tool paused", extra={"tool": name})
            raise
        except Exception:
            log.exception(
                "tool failed",
                extra={"tool": name, "ms": round((time.perf_counter() - started) * 1000, 1)},
            )
            raise

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        extra = {"tool": name, "ms": elapsed_ms}
        if name in WRITE_TOOLS:
            # 审计：写操作按 WARNING 记，方便单独筛出来
            extra["audit"] = True
            extra["path"] = args.get("path")
            log.warning("disk write", extra=extra)
        else:
            log.info("tool done", extra=extra)

        if isinstance(result, ToolMessage) and result.status == "error":
            log.warning("tool returned error", extra={"tool": name, **_error_digest(result)})
        return result


def _error_digest(message: ToolMessage) -> dict:
    """工具错误只记前 200 字符，避免把整段堆栈写进日志。"""
    return {"detail": message_text(message)[:200]}


def build_pii_middlewares() -> list:
    """输入侧的敏感信息脱敏：只处理「用户发进来的内容」。

    为什么只做输入侧：这个 Agent 还要读代码、生成代码，输出侧一旦开脱敏，
    生成的 URL、示例 IP、邮箱都会被替换成占位符，代码直接跑不起来。
    而输入侧的目标很明确 —— 别把用户的隐私顺手送进第三方模型 API。

    检测范围：
      - email / credit_card（内置，银行卡号带 Luhn 校验，误报低）
      - 中国大陆手机号、身份证号、sk- 开头的 API Key（自定义正则）

    注意：脱敏发生在「发给模型之前」，前端自己保存的原文不受影响。
    """
    return [
        _pii("email", strategy="mask"),
        _pii("credit_card", strategy="redact"),
        _pii("phone_cn", detector=r"1[3-9]\d{9}", strategy="mask"),
        _pii(
            "id_card_cn",
            detector=r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            strategy="mask",
        ),
        _pii("api_key", detector=r"sk-[A-Za-z0-9]{16,}", strategy="redact"),
    ]


def _pii(pii_type: str, *, strategy: str, detector: str | None = None):
    """PIIMiddleware 的薄封装：统一参数，避免每处都写一遍 apply_to_* 开关。"""
    return PIIMiddleware(
        pii_type,
        strategy=strategy,  # type: ignore[arg-type]
        detector=detector,
        apply_to_input=True,
        apply_to_output=False,  # 见 build_pii_middlewares 的说明
        apply_to_tool_results=False,  # 读进来的文件原文不该被改写
    )


class InputGuardMiddleware(AgentMiddleware):
    """输入护栏：拒绝空输入与超长消息。"""

    name = "input_guard"

    def before_agent(self, state, runtime):
        messages = state.get("messages") or []
        if not messages:
            raise ValueError("对话内容为空，请先输入内容再发送")

        # 从后往前找最近一条用户消息：中断恢复时末尾是 AI/工具消息，
        # 真正需要检查的还是那条人类输入。
        human = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
        )
        if human is None:
            return None

        length = len(message_text(human))
        if length > config.AGENT_MAX_INPUT_CHARS:
            raise ValueError(
                f"单条消息过长（{length} 字符，上限 {config.AGENT_MAX_INPUT_CHARS}），"
                "请拆分后再发送，或改用附件上传文件"
            )
        return None
