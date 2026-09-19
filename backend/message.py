import contextvars
import json
import os
import queue
import re
import threading
import uuid

from dotenv import load_dotenv
from flask import Blueprint, Response, jsonify, request, stream_with_context
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

import config
import db
from attachment import IMAGE_KEEP_TURNS, build_content_blocks
from AIagent.agents import SmartAssistant
from logging_setup import current_request_id, get_logger
from lowcode_auth import can_access, require_user
from lowcode_store import LowcodeError

load_dotenv(override=True)

log = get_logger("chat")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")
# 使用langchain统一初始化模型
# model = init_chat_model(
#     # model="deepseek-flash",
#     # model_provider="deepseek",
#     model='deepseek:deepseek-flash',
#     api_key=DEEPSEEK_API_KEY,
#     base_url=DEEPSEEK_API_BASE
# )


message_bp = Blueprint("message", __name__)


@message_bp.errorhandler(LowcodeError)
def _on_message_auth_error(exc: LowcodeError):
    """鉴权失败（未登录/令牌过期）统一 401 JSON，与低代码平台共用登录。"""
    return jsonify({"error": str(exc)}), exc.code

agent = SmartAssistant()

# SSE 响应头：禁止缓存 + 关闭反向代理缓冲（否则流会被攒起来一次性下发）
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# thread_id 由服务端生成（uuid4 hex，32 位）或前端原样回传，
# 这里只放行安全字符集 + 限长，避免恶意字符串被存进 checkpointer。
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sse_event(payload) -> str:
    """把一个 Python 对象编码成一条 SSE 消息（data: xxx\\n\\n）。"""
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _content_to_text(content) -> str:
    """把各种形态的 content 统一成字符串。

    str → 原样返回；
    [{'type': 'text', 'text': '...'}]（多模态分片）→ 拼接其中的文本。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def _chunk_text(chunk) -> str:
    """取出流式分片里的正文内容。"""
    return _content_to_text(getattr(chunk, "content", None))


def _chunk_reasoning(chunk) -> str:
    """取出思维链分片（DeepSeek/OpenAI 兼容接口放在 additional_kwargs.reasoning_content）。"""
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    value = kwargs.get("reasoning_content")
    return value if isinstance(value, str) else ""


# 前端 role → LangChain 消息类
_ROLE_TO_MESSAGE = {
    "system": SystemMessage,  # 系统提示词
    "user": HumanMessage,  # 用户说的话
    "assistant": AIMessage,  # 模型之前说过的话（多轮上下文靠它）
}


def _collect_attachment_ids(raw) -> list:
    """扫描所有消息，收集附件 id（用于一次性批量查库）。"""
    ids = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        refs = item.get("attachments")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, str):
                ids.append(ref)
            elif isinstance(ref, dict) and ref.get("id"):
                ids.append(str(ref["id"]))
    return ids


def _attachment_records(item, records_by_id: dict) -> list:
    """取出一条消息引用的附件记录（顺序与前端一致，查不到的忽略）。"""
    refs = item.get("attachments")
    if not isinstance(refs, list):
        return []
    records = []
    for ref in refs:
        key = ref if isinstance(ref, str) else (isinstance(ref, dict) and ref.get("id"))
        if key:
            record = records_by_id.get(str(key))
            if record:
                records.append(record)
    return records


def _attachment_refs(raw) -> list:
    """规整一条消息带来的附件引用，只留 id/name/kind/size 四个字段。

    只存引用（快照），不存文件本身：附件表才是唯一真相，模型要用的解析正文
    由 _to_messages 现场回查；这里的 name/kind/size 是给「重新打开老会话」
    直接渲染文件名和大小用的，省掉前端逐条回查附件接口。
    """
    if not isinstance(raw, list):
        return []
    refs = []
    for item in raw:
        if isinstance(item, str):
            refs.append({"id": item, "name": "", "kind": "other", "size": 0})
        elif isinstance(item, dict) and item.get("id"):
            refs.append(
                {
                    "id": str(item["id"]),
                    "name": str(item.get("name") or ""),
                    "kind": str(item.get("kind") or "other"),
                    "size": int(item.get("size") or 0),
                }
            )
    return refs


def _conversation_payload(conversation_id: str) -> list:
    """把库里存的会话历史还原成 _to_messages 认的格式。

    会话模式下「显示历史」与「发给模型的上下文」是同一份数据：
    模型看到的就是用户看到的，不会出现「界面上有、模型看不到」这类不一致。
    """
    return [
        {
            "role": message["role"],
            "content": message["content"],
            "attachments": message["attachments"],
        }
        for message in db.list_messages(conversation_id)
    ]


def _to_messages(data) -> list:
    """把一组消息字典（role/content/attachments）转成 LangChain 消息对象。

    两个来源共用这一份转换：
      - /send-message/stream：前端直接传上来的消息数组（无状态调试路径）；
      - 会话模式：从会话库重建的历史（_conversation_payload）。

    消息格式：
      [{"role": "user", "content": "...", "attachments": [{"id": "xxx"}]}, ...]

    附件处理（两步式的第二步）：
      - 图片：拼成多模态 content block（{"type": "image_url", ...}），
        但为了控制 token，只保留最近 IMAGE_KEEP_TURNS 轮里出现过的图片；
        更早的图片降级成一行文字占位。
      - 文档/表格/文本：注入解析好的正文（带长度上限）。
    """
    raw = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    records_by_id = db.get_attachments(_collect_attachment_ids(raw))

    # 倒推最后 IMAGE_KEEP_TURNS 条「带图片」的消息下标，只有它们保留图片块
    image_indexes = [
        index
        for index, item in enumerate(raw)
        if isinstance(item, dict)
        and any(
            r.get("kind") == "image" for r in _attachment_records(item, records_by_id)
        )
    ]
    keep_images = (
        set(image_indexes[-IMAGE_KEEP_TURNS:]) if IMAGE_KEEP_TURNS > 0 else set()
    )

    messages = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = _content_to_text(item.get("content"))
        records = _attachment_records(item, records_by_id)
        # 纯附件、不带文字的消息也要发出去（比如"看看这个"只发了图）
        if not text.strip() and not records:
            continue
        # 未知 role 兜底成 user，避免整条历史被丢掉
        role = str(item.get("role") or "user").lower()
        content = build_content_blocks(
            text, records, include_images=index in keep_images
        )
        messages.append(_ROLE_TO_MESSAGE.get(role, HumanMessage)(content))
    return messages


def _run_agent_stream(run, thread_id, on_flush=None, interrupt_extra=None):
    """执行一次 agent（新对话或中断恢复），产出 SSE 事件。

    run：无参可调用对象，返回 agent 的流式迭代器。用可调用对象而不是现成的
        迭代器，是为了让下面的 try 能把「调用即抛错」也一并管住。

    on_flush：可选回调 (text, reasoning) -> None，会话模式用它把「已生成但还
        没落库」的内容写进数据库。调用时机有两个：
          1) 检测到挂起（发 interrupt 之前）——先落库再通知前端，这样挂起期间
             刷新页面也能看到已经生成的部分；
          2) 流结束（正常、报错、用户点停止都会走到 finally）。
        回调返回后缓冲区清空，所以同一段内容不会写两遍；没有内容时不回调。

    interrupt_extra：附加到 interrupt 事件上的字段。会话模式带 message_id，
        前端续跑审批时原样传回，服务端据此把续写内容接到同一条回复上。

    事件格式（每行以 data: 开头，空行结尾）：
      {"type": "reasoning", "content": "..."}   思维链（可忽略不渲染）
      {"type": "content",   "content": "..."}   正文
      {"type": "todos",     "todos": [{"content", "status"}]}
                                                 待办清单（TodoListMiddleware 产出）
      {"type": "interrupt", "thread_id": "...",   工具调用挂起，等待人工审批
       "actions": [{"name", "args", "description", "allowed_decisions"}],
       "message_id": 123}                        会话模式额外带上助手消息 id
      {"type": "usage",     "usage": {...}}     用量统计，仅在末尾出现一次
      {"type": "error",     "error": "..."}     生成中途出错
      [DONE]                                    结束标记
    """
    usage = None
    waiting_approval = False
    # 客户端断开（点停止/关页面）时会在 yield 处抛 GeneratorExit，用它把
    # 「这次是被强行关掉的」记下来：已生成内容要照常落库，但绝不能再往外 yield。
    is_closed = False
    # 攒流式分片、flush 时一次性落库（每条事件都写一次库代价太高）
    text_buf: list[str] = []
    reasoning_buf: list[str] = []

    def flush() -> None:
        if on_flush is None:
            return
        text = "".join(text_buf)
        reasoning = "".join(reasoning_buf)
        text_buf.clear()
        reasoning_buf.clear()
        if not text and not reasoning:
            return
        try:
            on_flush(text, reasoning)
        except Exception:  # noqa: BLE001 - 落库失败不能连累正在下发的流
            log.exception("persist reply failed", extra={"thread_id": thread_id})

    try:
        # agent.stream() / agent.resume() 内部已经处理了两件事（见 AIagent/agents.py）：
        #   1) 入参包装成 {"messages": [...]}（LangGraph 编译图只认状态字典，
        #      直接传消息列表会抛 InvalidUpdateError：
        #      Expected dict, got [HumanMessage(...)]（INVALID_GRAPH_NODE_RETURN_VALUE）
        #      或 Command(resume=...)）；
        #   2) stream_mode="messages"——只有它才逐 token 产出；
        #      默认的 "updates" 是按节点聚合的 dict，取 content 会一直是空。
        # 该模式下每个元素是 (消息分片, 元数据) 二元组，所以要解包。
        for chunk, _metadata in run():
            # 该模式会把「工具节点」的产物（ToolMessage）也一并产出，
            # 实测 (节点='tools', 类型=ToolMessage) 会出现，内容形如 "1+1 = 2"。
            # 那是给模型看的，不是给用户看的正文，所以只保留模型自己的分片。
            if not isinstance(chunk, AIMessageChunk):
                continue

            thinking = _chunk_reasoning(chunk)
            if thinking:
                reasoning_buf.append(thinking)
                yield sse_event({"type": "reasoning", "content": thinking})

            text = _chunk_text(chunk)
            if text:
                text_buf.append(text)
                yield sse_event({"type": "content", "content": text})

            # usage 只在最后一个分片里，这里持续记录，循环结束后再下发
            if getattr(chunk, "usage_metadata", None):
                usage = chunk.usage_metadata

        # stream_mode="messages" 不会带出中断信息，所以流跑完后回来问一次图状态。
        # 待办清单同理（TodoListMiddleware 写在 state 的 "todos" 键上）。
        # 每轮工具审批前后都会跑到这里，所以前端能持续看到进度更新。
        todos = agent.pending_todos(thread_id)
        if todos:
            yield sse_event({"type": "todos", "todos": todos})

        # 有挂起动作 = 这轮还没结束，要等前端把审批结果提交回来（走 resume 接口）；
        # 此时绝不能删会话检查点，否则恢复时找不到那个挂起的线程。
        actions = agent.pending_actions(thread_id)
        if actions:
            waiting_approval = True
            flush()  # 先落库再发中断：挂起期间刷新页面也能看到已生成的部分
            event = {"type": "interrupt", "thread_id": thread_id, "actions": actions}
            if interrupt_extra:
                event.update(interrupt_extra)
            yield sse_event(event)
    except GeneratorExit:
        # 客户端断开 / werkzeug 收尾时会关闭这个生成器：记下来并原样抛出。
        # 关键是不能在 close() 流程里再 yield（哪怕只是 [DONE]），
        # 否则 Python 会报 RuntimeError: generator ignored GeneratorExit。
        is_closed = True
        raise
    except Exception:
        # 已经发出了 200，无法再改状态码，只能把错误作为一条事件下发。
        # 服务端记完整堆栈，下发给前端的只给一句人话 + 请求 ID，
        # 方便用户报错时直接报 ID 定位，又不泄露内部实现。
        log.exception("stream failed", extra={"thread_id": thread_id})
        yield sse_event(
            {
                "type": "error",
                "error": "生成失败，请重试；若反复出现请把请求 ID 一并发给管理员",
                "request_id": current_request_id(),
            }
        )
    finally:
        if usage and not is_closed:
            yield sse_event({"type": "usage", "usage": usage})
        # 正常结束、报错、客户端断开都会走到这里：已生成的内容要落库，
        # 不能因为「没生成完」就丢掉（后续续跑会接着往同一条消息上追加）。
        flush()
        if not waiting_approval:
            # 这轮真的结束了，释放会话检查点，避免 InMemorySaver 里的线程无限增长
            agent.release_thread(thread_id)
        log.info(
            "stream done",
            extra={"thread_id": thread_id, "waiting_approval": waiting_approval},
        )
        if not is_closed:
            yield sse_event("[DONE]")  # 约定结束标记，前端据此关闭连接


def _with_heartbeat(events, interval: float):
    """包装事件生成器：空闲超过 interval 秒就发一行 SSE 注释当心跳。

    为什么需要：模型「想很久」（长思维链、多次工具调用）时连接上可能几十秒没有
    任何字节，nginx / 公司网关 / 浏览器的空闲超时会把连接当成死连接掐掉，
    前端表现就是「聊到一半突然断流」。发一行 `: ping`（SSE 注释，客户端会忽略）
    既保活又不影响协议。

    实现上必须另开线程去跑原生成器：它是阻塞的，在主线程里没法同时等 token
    和计时。注意用 copy_context() 把请求上下文带过去，否则 Agent 内部打的日志
    会丢掉 request_id，排查时对不上号。
    """
    if interval <= 0:
        yield from events
        return

    pending: queue.Queue = queue.Queue()
    done = object()
    stop = threading.Event()
    context = contextvars.copy_context()

    def pump() -> None:
        try:
            for event in events:
                if stop.is_set():
                    break
                pending.put(event)
        except Exception as exc:  # noqa: BLE001 - 原生成器已自行兜底，这里再防守一层
            pending.put(exc)
        finally:
            pending.put(done)

    worker = threading.Thread(target=context.run, args=(pump,), daemon=True)
    worker.start()
    try:
        while True:
            try:
                item = pending.get(timeout=interval)
            except queue.Empty:
                yield ": ping\n\n"  # SSE 注释行：浏览器/EventSource 直接丢弃
                continue
            if item is done:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        # 客户端断开（GeneratorExit）时也要通知后台线程别再抢着拉流了。
        # 它在阻塞的底层迭代里不会立刻响应，所以线程设为 daemon，随进程退出。
        stop.set()


def _sse_response(events) -> Response:
    """把事件生成器包成一个 SSE 响应。

    生成器跑在请求上下文之外，所以必须包 stream_with_context；
    外层再套一层心跳，长时间没 token 也不会被中间代理误当成死连接。
    """
    return Response(
        stream_with_context(_with_heartbeat(events, config.SSE_HEARTBEAT_SECONDS)),
        mimetype="text/event-stream",
        headers=SSE_HEADERS,
    )


def _read_thread_id(data) -> tuple:
    """读取并校验请求体里的 thread_id。

    返回 (thread_id, error)：校验不过时 thread_id 为 None、error 是要直接返回
    的 400 响应；通过时 error 为 None。
    """
    thread_id = str(data.get("thread_id") or "").strip()
    if not thread_id or not _THREAD_ID_RE.match(thread_id):
        return None, (
            jsonify(
                {"error": "thread_id 为必填，且只允许字母/数字/下划线/短横线，最长 64 位"}
            ),
            400,
        )
    return thread_id, None


def release_conversation_threads(conversation_id: str) -> int:
    """清掉某个会话还挂在 checkpointer 里的线程（删会话时调用）。

    检查点里存的是「挂起中的工具调用、待办清单」这类运行状态；
    对话本身都要删了，这些状态也就没意义了，不主动清要等到进程重启才释放。
    正常跑完的线程早就释放过，重复清是安全的。
    """
    thread_ids = db.list_thread_ids(conversation_id)
    for thread_id in thread_ids:
        agent.release_thread(thread_id)
    return len(thread_ids)


@message_bp.route("/send-message/stream", methods=["POST"])
def send_message_stream():
    """流式对话（无状态模式）：模型产出一点，就立刻推给前端一点（SSE）。

    请求体：{"messages": [...]}，可选 {"thread_id": "..."}。
    服务端每轮自己生成 thread_id，需要时会随 interrupt 事件下发，
    前端提交审批时原样带回即可。

    这里每轮都由前端回传全量历史，历史不落库——只留给 stream_demo.html 这类
    调试页面用；主应用走 /conversations/<id>/messages（历史存服务端）。
    """
    data = request.get_json(silent=True) or {}
    messages = _to_messages(data)
    if not messages:
        return jsonify({"error": "messages 为必填，且必须是数组"}), 400

    # 每轮一个新 thread_id：前端每次都回传全量历史，
    # 复用同一个 thread_id 会让 checkpointer 里的旧状态与之叠加，消息重复。
    # 这个 id 不需要提前告知前端——需要时它会随 interrupt 事件一起下发。
    incoming = str(data.get("thread_id") or "").strip()
    if incoming and not _THREAD_ID_RE.match(incoming):
        return jsonify({"error": "thread_id 格式非法（只允许字母/数字/下划线/短横线，最长 64 位）"}), 400
    thread_id = incoming or uuid.uuid4().hex

    log.info("stream start", extra={"thread_id": thread_id, "messages": len(messages)})
    return _sse_response(
        _run_agent_stream(lambda: agent.stream(messages, thread_id), thread_id)
    )


@message_bp.route("/send-message/resume", methods=["POST"])
def resume_message_stream():
    """提交人工审批结果，继续被挂起的对话（SSE，事件格式同上）。

    请求体：{"thread_id": "...", "decisions": [{"type": "approve"}, ...]}

    decisions 是「按位置对应」的：顺序必须和中断事件里 actions 的顺序一致，
    长度也必须相等，否则中间件会抛
    ValueError: Number of human decisions (x) does not match number of
    hanging tool calls (y)。
    """
    data = request.get_json(silent=True) or {}
    decisions = data.get("decisions")
    thread_id, error = _read_thread_id(data)
    if error:
        return error
    if not isinstance(decisions, list) or not decisions:
        return jsonify({"error": "decisions 为必填，且必须是非空数组"}), 400

    log.info(
        "resume start",
        extra={"thread_id": thread_id, "decisions": len(decisions)},
    )
    return _sse_response(
        _run_agent_stream(lambda: agent.resume(thread_id, decisions), thread_id)
    )


@message_bp.route("/conversations/<string:conversation_id>/messages", methods=["POST"])
def send_conversation_message(conversation_id):
    """会话内发一条消息（SSE，事件格式同 /send-message/stream）。

    请求体：{"content": "...", "attachments": [{"id": "..."}]}

    与 /send-message/stream 的区别在于「历史放在哪」：
      1. 前端只传本轮新消息，不再每轮回传全量历史——传输量恒定，
         也没人能伪造 assistant 历史来绕过护栏；
      2. 用户消息先落库再调模型：模型报错、进程挂掉，用户说过的话不丢；
      3. 上下文从库里重建（显示历史 = 模型上下文，同一份数据）；
      4. 生成过程中断或结束，已生成的内容都追加回库（见 _run_agent_stream），
         刷新页面、换设备都能接着看。
    """
    user = require_user()
    conversation = db.get_conversation(conversation_id)
    if conversation is None or not can_access(user, conversation.get("owner_id", "")):
        return jsonify({"error": f"会话 {conversation_id} 不存在"}), 404

    data = request.get_json(silent=True) or {}
    content = _content_to_text(data.get("content")).strip()
    attachments = _attachment_refs(data.get("attachments"))
    if not content and not attachments:
        return jsonify({"error": "content 与 attachments 至少要有一个"}), 400

    # 1) 用户消息先落库：模型报错、进程挂掉，用户说过的话都不丢
    db.add_message(conversation_id, "user", content, attachments=attachments)

    # 标题：还挂着默认标题、且本条有文字时，用它生成会话标题。
    # 不限定「首条」：第一条只发了附件（没文字）的话，第二条带文字的补上也行。
    if content and conversation["title"] == db.DEFAULT_TITLE:
        db.rename_conversation(conversation_id, db.make_title(content))

    # 2) 组装上下文：直接从库里读，刚写的这条自然也在其中
    messages = _to_messages({"messages": _conversation_payload(conversation_id)})
    if not messages:
        return jsonify({"error": "会话里没有可发送的内容"}), 400

    # 3) 先建一条空的助手消息占位：生成被打断/停止时，已生成的内容有地方落库；
    #    message_id 随 interrupt 事件下发，续跑审批时前端原样带回。
    #    thread_id 也记在这一行上：删会话时靠它把 checkpointer 里的挂起状态清掉。
    thread_id = uuid.uuid4().hex
    reply = db.add_message(conversation_id, "assistant", "", thread_id=thread_id)

    log.info(
        "conversation stream start",
        extra={"conversation_id": conversation_id, "thread_id": thread_id},
    )
    return _sse_response(
        _run_agent_stream(
            lambda: agent.stream(messages, thread_id),
            thread_id,
            on_flush=lambda text, reasoning: db.append_message(
                reply["id"], text, reasoning
            ),
            interrupt_extra={"message_id": reply["id"]},
        )
    )


@message_bp.route(
    "/conversations/<string:conversation_id>/messages/resume", methods=["POST"]
)
def resume_conversation_message(conversation_id):
    """会话内提交人工审批结果并续跑（SSE，事件格式同上）。

    请求体：{"thread_id": "...", "decisions": [...], "message_id": 123}
    message_id 取 interrupt 事件里带回的值：续写的内容接到同一条助手回复上
    （所以刷新页面后，看到的是拼接完整的整条回复，而不是两个半截）。
    """
    user = require_user()
    conversation = db.get_conversation(conversation_id)
    if conversation is None or not can_access(user, conversation.get("owner_id", "")):
        return jsonify({"error": f"会话 {conversation_id} 不存在"}), 404
    data = request.get_json(silent=True) or {}
    thread_id, error = _read_thread_id(data)
    if error:
        return error
    decisions = data.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return jsonify({"error": "decisions 为必填，且必须是非空数组"}), 400
    message_id = data.get("message_id")
    if not isinstance(message_id, int):
        return jsonify({"error": "message_id 为必填，取 interrupt 事件里带回的值"}), 400

    # 校验消息确实属于这个会话，防止把 A 会话的续写写进 B 会话
    message = db.get_message(message_id)
    if message is None or message["conversation_id"] != conversation_id:
        return jsonify({"error": f"消息 {message_id} 不属于会话 {conversation_id}"}), 400
    # 消息上记的线程要和对上的线程一致（老数据没记录时跳过这层校验）
    if message.get("thread_id") and message["thread_id"] != thread_id:
        return jsonify({"error": "thread_id 与这条消息记录的不一致，请重新打开对话再试"}), 400

    log.info(
        "conversation resume start",
        extra={"conversation_id": conversation_id, "thread_id": thread_id},
    )
    return _sse_response(
        _run_agent_stream(
            lambda: agent.resume(thread_id, decisions),
            thread_id,
            on_flush=lambda text, reasoning: db.append_message(
                message_id, text, reasoning
            ),
            interrupt_extra={"message_id": message_id},
        )
    )
