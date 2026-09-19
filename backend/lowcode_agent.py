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

lowcode_agent_bp = Blueprint("lowcode_agent", __name__)

MAX_ROUNDS = 3
MAX_INSTRUCTION_CHARS = 2000
MAX_MATERIALS = 100


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
    """从流式分片里取纯文本（兼容 content 为字符串或分片列表两种形态）。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _validate(schema) -> list[str]:
    if not isinstance(schema, dict):
        return ["schema 必须是 JSON 对象"]
    errors = validate_schema(schema)
    if not errors and len(json.dumps(schema, ensure_ascii=False)) > MAX_SCHEMA_BYTES:
        errors.append(f"schema 体积超过上限（{MAX_SCHEMA_BYTES // 1024} KB）")
    return errors


def _outline(schema: dict) -> str:
    """当前画布的节点 id 一览（给模型看的「地图」，增量修改靠它定位节点）。"""
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
    catalog = json.dumps(materials, ensure_ascii=False)
    parts = [f"【用户需求】\n{instruction}"]
    if isinstance(current_schema, dict):
        parts.append(
            "【当前画布 Schema】（修改类需求请用 submit_patch 增量改；全新页面可忽略）\n"
            + json.dumps(current_schema, ensure_ascii=False)
        )
        parts.append("【当前画布结构（节点 id 一览）】\n" + _outline(current_schema))
    parts.append(f"【可用物料目录】\n{catalog}")
    return "\n\n".join(parts)


def generate_events(instruction: str, materials: list, current_schema):
    """生成器：产出 SSE 事件字典（供 api 层包成 data 帧）。"""
    binder = model.bind_tools([submit_page_schema, submit_patch])
    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(_human_prompt(instruction, materials, current_schema)),
    ]

    for round_index in range(MAX_ROUNDS):
        yield {"type": "status", "text": f"模型生成中（第 {round_index + 1} 轮）…"}

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

        tool_calls = list(getattr(aggregated, "tool_calls", None) or [])
        if not tool_calls:
            yield {
                "type": "error",
                "message": "模型没有提交修改（未调用 submit_page_schema / submit_patch），请换个说法重试",
            }
            return

        call = tool_calls[0]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        messages.append(AIMessage(content="".join(text_parts), tool_calls=[call]))

        if call.get("name") == "submit_patch":
            patches = args.get("patches")
            if not isinstance(current_schema, dict):
                errors = ["当前没有可修改的页面，请改用 submit_page_schema 生成整页"]
                applied = None
            else:
                applied, errors = apply_patches(current_schema, patches)
            if not errors:
                yield {
                    "type": "patch",
                    "patches": patches,
                    "schema": applied,
                    "count": len(patches),
                }
                return
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

        schema = args.get("page_schema")
        errors = _validate(schema)
        if not errors:
            messages.append(ToolMessage(content="校验通过", tool_call_id=call["id"]))
            yield {"type": "schema", "schema": schema}
            return

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

    yield {"type": "error", "message": "多轮校验仍未通过，已停止。可以把需求拆小后重试。"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@lowcode_agent_bp.post("/lowcode/agent/stream")
def api_agent_stream():
    require_user()
    data = request.get_json(silent=True) or {}
    instruction = (data.get("instruction") or "").strip()
    if not instruction:
        raise store.LowcodeError("instruction 必填")
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise store.LowcodeError(f"描述太长（上限 {MAX_INSTRUCTION_CHARS} 字）")
    materials = data.get("materials") if isinstance(data.get("materials"), list) else []
    materials = materials[:MAX_MATERIALS]
    current_schema = data.get("schema") if isinstance(data.get("schema"), dict) else None

    def stream():
        try:
            for event in generate_events(instruction, materials, current_schema):
                yield _sse(event)
            yield _sse({"type": "done"})
        except GeneratorExit:
            raise
        except Exception as exc:  # noqa: BLE001 - 任何失败都要让前端看到原因
            yield _sse({"type": "error", "message": f"生成失败：{exc}"})
            yield _sse({"type": "done"})

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
