# -*- coding: utf-8 -*-
"""AI 生成：自然语言 → 页面 Schema（全量生成，也可以带上当前画布做修改）。

实现思路（与 AIagent/manual_loop.py 同一套路的手动工具循环）：
   系统提示词（DSL 规则） → 模型调用 submit_page_schema → 后端校验
   → 不过就把错误喂回模型自修（最多 3 轮）→ 通过则下发最终 schema。

SSE 事件（data 帧，与 message.py 的约定一致）：
   status  生成进度提示
   text    模型解说（逐 token 流式）
   schema  校验通过的 PageSchema，前端应用到画布（可撤销）
   error   失败原因
   done    结束标记

两个 Function Calling 工具：
    submit_page_schema  整页生成（传完整 page_schema）；
    submit_patch        增量改页（传语义化 patches，不改就不动其它节点）。
模型校验不过时，后端把错误用 ToolMessage 回喂，让它自己改——最多 MAX_ROUNDS 轮自检。
"""

import json

from flask import Blueprint, Response, jsonify, request, stream_with_context
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

import lowcode_store as store
from AIagent.llm import model
from lowcode_auth import require_user
from lowcode_patch import apply_patches
from lowcode_schema import MAX_SCHEMA_BYTES, validate_schema
from rich import print

lowcode_agent_bp = Blueprint("lowcode_agent", __name__)

# 模型自检的最大轮数：防校验死循环（模型反复改不对就停下来让用户拆需求）
MAX_ROUNDS = 3
# 用户自然语言描述的字符上限：防超长 prompt 打爆上下文/计费
MAX_INSTRUCTION_CHARS = 2000
# 喂给模型的物料目录条数上限：物料太多会稀释注意力、也撑爆上下文
MAX_MATERIALS = 100


# 统一把业务异常翻成 {"error": ...} + 对应 HTTP 状态码
@lowcode_agent_bp.errorhandler(store.LowcodeError)
def _on_agent_error(exc: store.LowcodeError):
    return jsonify({"error": str(exc)}), exc.code


SYSTEM_PROMPT = """你是一名低代码页面生成专家。根据用户描述生成页面 Schema，并调用工具提交。

Schema 结构（严格遵守）：
{
  "schemaVersion": "1.0",
  "name": "页面名",
  "state": { "变量名": 初始值 },
  "dataSources": [
    { "id": "userList", "kind": "rest", "method": "GET|POST|PUT|DELETE",
      "url": "/users", "params": { "key": "{{ state.keyword }}" }, "autoLoad": true }
  ],
  "root": 组件节点
}
组件节点：
{ "id": "唯一英文id", "type": "物料type", "props": { ... },
  "visible": "{{ 表达式 }}"（可选）, "children": [子节点],
  "events": { "onClick": { "kind": "callDataSource", "dataSource": "userList",
              "params": {...}, "onSuccess": [...], "onError": [...] } } }

动作 kind 只能是：callDataSource / setState / navigate / notify，字段如下：
  { "kind": "callDataSource", "dataSource": "数据源id", "params": { ... },
    "onSuccess": [后续动作数组，可选], "onError": [...] }
  { "kind": "setState", "key": "变量名", "value": "{{ event.value }}" }
  { "kind": "navigate", "to": "/路径" }
  { "kind": "notify", "text": "提示文案", "level": "success"（可选） }
表达式写在字符串里，用 {{ }} 包裹；可用上下文：
  state.xxx、dataSources.<id>.data、dataSources.<id>.loading、event.value、env.user。
只能使用物料目录里列出的 type、props 和事件名，不要发明不存在的属性。
根节点 id 固定为 "root"。表格数据绑定写成 "dataSource": "{{ dataSources.<id>.data }}"，
加载态写 "loading": "{{ dataSources.<id>.loading }}"。
页面尽量完整可用：有数据源就配 autoLoad 或按钮触发，配套搜索/表格等组件。

流程要求，两种工具按场景二选一：
1) 新建页面 / 整页重做 → submit_page_schema（完整 Schema 放参数 page_schema）；
2) **修改已有页面 → submit_patch**（只动需要变的节点，绝不整页重生成），
   patches 是语义化操作数组，支持：
   {"op":"update_props","nodeId":"table","props":{"rowKey":"id"}}   （值为 null 删除该属性）
   {"op":"set_visible","nodeId":"btn","visible":"{{ env.role === 'admin' }}"}      （null = 取消条件）
   {"op":"set_event","nodeId":"btn","event":"onClick","action":{...}}              （null = 清除事件）
   {"op":"add_node","parentId":"root","node":{完整节点},"index":0}
   {"op":"remove_node","nodeId":"statTotal"}
   {"op":"move_node","nodeId":"card1","parentId":"row2"}
   {"op":"set_state","key":"keyword","value":""}                                   （null = 删变量）
   {"op":"upsert_data_source","dataSource":{完整数据源}}
   {"op":"remove_data_source","id":"userList"}
   {"op":"set_page_name","name":"客户管理"}
   nodeId 要用【当前画布结构】里的真实 id；新增节点的 id 不能与现有重复。

校验失败时会收到错误列表，请修正后再次调用同一个工具。
生成前用一两句中文说明你的改动思路。"""


# 两个 Function Calling 工具：@tool 装饰器会把函数签名/docstring 转成模型可读的工具定义。
# 这里的 return 只是占位话术——真正的校验发生在后端拿到 args 之后，工具本身不做校验。

@tool
def submit_page_schema(page_schema: dict) -> str:
    """提交生成好的页面 Schema（完整 JSON 对象）。

    Args:
        page_schema: 完整的页面 Schema，结构见系统提示词。
    """
    return "已收到，等待校验。"


@tool
def submit_patch(patches: list) -> str:
    """提交一组增量修改（只改需要动的节点，不整页重生成）。

    Args:
        patches: 语义化操作数组，例如
            [{"op": "update_props", "nodeId": "title", "props": {"text": "客户管理"}}]，
            可用操作见系统提示词。
    """
    return "已收到，等待校验。"


def _chunk_text(chunk) -> str:
    """从流式分片里取纯文本（兼容 content 为字符串或分片列表两种形态）。

    不同模型/版本的流式 content 形态不一：有的直接给字符串，有的给
    [{type:text, text:...}] 分片列表，这里统一压成一段文本供 SSE 逐 token 下发。
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _validate(schema) -> list[str]:
    """整页生成的校验：结构合法性 + 体积上限。体积检查放在结构过了之后，
    避免在明显坏掉的 Schema 上白白序列化大字符串。"""
    if not isinstance(schema, dict):
        return ["schema 必须是 JSON 对象"]
    errors = validate_schema(schema)
    if not errors and len(json.dumps(schema, ensure_ascii=False)) > MAX_SCHEMA_BYTES:
        errors.append(f"schema 体积超过上限（{MAX_SCHEMA_BYTES // 1024} KB）")
    return errors


def _outline(schema: dict) -> str:
    """当前画布的节点 id 一览（给模型看的「地图」，增量修改靠它定位节点）。

    只输出 缩进 + id:type 的树，不输出 props 全文——模型改 patch 时只需要知道
    「有哪些节点、分别是什么类型」来选 nodeId，不用重新理解整份 props。
    """
    lines: list[str] = []

    def walk(node, depth: int) -> None:
        if not isinstance(node, dict):
            return
        lines.append("  " * depth + f"- {node.get('id')}: {node.get('type')}")
        for child in node.get("children") or []:
            walk(child, depth + 1)

    walk(schema.get("root") or {}, 0)
    return "\n".join(lines)


def _human_prompt(instruction: str, materials: list, current_schema) -> str:
    """拼装本轮 HumanMessage：用户需求 + 当前画布（改页时）+ 可用物料目录。

    三份上下文缺一不可：需求是目标，画布是「现状」（决定走 patch 还是整页），
    物料目录是模型能选的 type/props 白名单。
    """
    catalog = json.dumps(materials, ensure_ascii=False)
    parts = [f"【用户需求】\n{instruction}"]
    # 只有「改页」场景才把当前画布喂进去；全新生成时 current_schema 为空
    if isinstance(current_schema, dict):
        parts.append(
            "【当前画布 Schema】（修改类需求请用 submit_patch 增量改；全新页面可忽略）\n"
            + json.dumps(current_schema, ensure_ascii=False)
        )
        parts.append("【当前画布结构（节点 id 一览）】\n" + _outline(current_schema))
    parts.append(f"【可用物料目录】\n{catalog}")
    return "\n\n".join(parts)


def generate_events(instruction: str, materials: list, current_schema):
    """生成器：产出 SSE 事件字典（供 api 层包成 data 帧）。

    多轮自检主循环：
        每轮把 messages 交给带工具绑定的模型流式跑一次 ->
        收齐后取出模型发起的第一个工具调用 ->
        后端校验它提交的 schema/patches：
            通过 -> 下发 schema/patch 事件并结束；
            失败 -> 把错误塞进 ToolMessage 追加进 messages，进入下一轮让模型自修。
    messages 全程累积，模型能看到自己上一轮说了什么、错在哪。
    """
    # bind_tools：把两个 @tool 绑到模型上，模型才知道可以发起这两次函数调用
    binder = model.bind_tools([submit_page_schema, submit_patch])
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(_human_prompt(instruction, materials, current_schema)),
    ]

    for round_index in range(MAX_ROUNDS):
        yield {"type": "status", "text": f"模型生成中（第 {round_index + 1} 轮）…"}

        # 流式：边收边把文本片实时推给前端；同时把整轮消息累加成 aggregated 以取 tool_calls
        aggregated = None
        text_parts: list[str] = []
        for chunk in binder.stream(messages):
            aggregated = chunk if aggregated is None else aggregated + chunk
            piece = _chunk_text(chunk)
            if piece:
                text_parts.append(piece)
                yield {"type": "text", "content": piece}
        if aggregated is None:
            yield {"type": "error", "message": "模型没有返回内容，请重试"}
            return

        # 模型这一轮必须发起工具调用，否则等于没产出可落地的东西
        tool_calls = list(getattr(aggregated, "tool_calls", None) or [])
        if not tool_calls:
            yield {
                "type": "error",
                "message": "模型没有提交修改（未调用 submit_page_schema / submit_patch），请换个说法重试",
            }
            return

        # 这里只取第一个工具调用：一个回合让模型只做一件事，便于定位和回喂错误
        call = tool_calls[0]
        print('call--->', call)
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        # 把模型这轮的回复（解说文本 + 工具调用）写回 messages，供下一轮它自己看到
        messages.append(AIMessage(content="".join(text_parts), tool_calls=[call]))

        # ---- 分支一：增量改页（submit_patch）----
        if call.get("name") == "submit_patch":
            patches = args.get("patches")
            # 没给当前画布却想用 patch 改页，属于模型误用工具，回喂让它改走整页
            if not isinstance(current_schema, dict):
                errors = ["当前没有可修改的页面，请改用 submit_page_schema 生成整页"]
                applied = None
            else:
                # apply_patches 自己会深拷贝 + 末尾全量校验，这里只拿结果
                applied, errors = apply_patches(current_schema, patches)
            if not errors:
                # 校验通过：把补丁和应用后的新 schema 一起下发，前端可直接套用/撤销
                yield {
                    "type": "patch",
                    "patches": patches,
                    "schema": applied,
                    "count": len(patches),
                }
                return
            # 校验失败：挑前几条错误用 ToolMessage 回喂，下一轮模型据此自修
            yield {
                "type": "status",
                "text": "增量修改校验未通过，正在让模型修正：" + "；".join(errors[:3]),
            }
            messages.append(
                ToolMessage(
                    content="patches 校验失败："
                    + "；".join(errors[:6])
                    + "。请修正后再次调用 submit_patch（只动需要改的节点）。",
                    tool_call_id=call["id"],
                )
            )
            continue

        # ---- 分支二：整页生成（submit_page_schema）----
        schema = args.get("page_schema")
        errors = _validate(schema)
        if not errors:
            # 校验通过：给工具调用一个成功回执，再把 schema 下发后结束
            messages.append(ToolMessage(content="校验通过", tool_call_id=call["id"]))
            yield {"type": "schema", "schema": schema}
            return

        # 校验失败：同样用 ToolMessage 把错误回喂，进下一轮
        yield {
            "type": "status",
            "text": "校验未通过，正在让模型修正：" + "；".join(errors[:3]),
        }
        messages.append(
            ToolMessage(
                content="schema 校验失败："
                + "；".join(errors[:6])
                + "。请修正后再次调用 submit_page_schema。",
                tool_call_id=call["id"],
            )
        )

    # 走完 MAX_ROUNDS 轮还没通过：主动止损，提示用户拆小需求
    yield {"type": "error", "message": "多轮校验仍未通过，已停止。可以把需求拆小后重试。"}


def _sse(payload: dict) -> str:
    """把事件字典编码成一条 SSE data 帧（JSON + 双换行结尾，标准 SSE 格式）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@lowcode_agent_bp.post("/lowcode/agent/stream")
def api_agent_stream():
    """AI 生成/改页的 SSE 流式入口。

    请求体：{instruction 自然语言, materials 物料目录, schema 当前画布(改页时)}。
    响应：text/event-stream，逐 token 推 text/status/patch/schema，结束推 done。
    """
    require_user()
    data = request.get_json(silent=True) or {}
    instruction = (data.get("instruction") or "").strip()
    if not instruction:
        raise store.LowcodeError("instruction 必填")
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise store.LowcodeError(f"描述太长（上限 {MAX_INSTRUCTION_CHARS} 字）")
    # materials 必须是数组且截断到上限，防止超大物料列表塞进模型上下文
    materials = data.get("materials") if isinstance(data.get("materials"), list) else []
    materials = materials[:MAX_MATERIALS]
    # 改页时由前端带上当前画布 schema；缺省/类型不对就当全新生成
    current_schema = data.get("schema") if isinstance(data.get("schema"), dict) else None

    def stream():
        try:
            for event in generate_events(instruction, materials, current_schema):
                yield _sse(event)
            yield _sse({"type": "done"})
        except GeneratorExit:
            # 客户端断开（关页/停生成）：原样抛出让 Flask 清理，不要被下面的兜底吞掉
            raise
        except Exception as exc:  # noqa: BLE001 - 任何失败都要让前端看到原因
            # 兜底：任何未预期异常都转成一条 error 帧再补 done，避免前端无限挂着
            yield _sse({"type": "error", "message": f"生成失败：{exc}"})
            yield _sse({"type": "done"})

    # stream_with_context：在请求上下文里跑生成器，确保 generate_events 内能访问 request
    # X-Accel-Buffering: no：让 Nginx 不缓冲，立刻把每帧推给前端（否则流式变假流）
    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
