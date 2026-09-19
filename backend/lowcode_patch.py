# -*- coding: utf-8 -*-
"""AI 增量修改的「语义化 Patch」指令集：校验 + 应用（后端权威实现）。

为什么不用 RFC 6902 的 JSON Pointer：
    组件树里的节点都有短 id（table / btn），模型按 id 操作比按 "/root/children/2/..."
    这种下标路径可靠得多——下标还会因为插删而整体错位。所以这里定义一组
    按节点 id 的语义化操作，前端的命令层（schema/patch.ts）与之一一对应。

支持的操作（patches 是数组，按顺序应用，最多 50 条）：
    {"op": "update_props",       "nodeId": ..., "props": {...}}     合并 props；值为 null 表示删除该属性
    {"op": "set_visible",        "nodeId": ..., "visible": "{{...}}"|null}
    {"op": "set_event",          "nodeId": ..., "event": "onClick", "action": {...}|null}
    {"op": "add_node",           "parentId": ..., "node": {...}, "index": 0}
    {"op": "remove_node",        "nodeId": ...}                     根节点不可删
    {"op": "move_node",          "nodeId": ..., "parentId": ..., "index": 0}
    {"op": "set_state",          "key": ..., "value": ...}          value 为 null 表示删除变量
    {"op": "upsert_data_source", "dataSource": {...}}
    {"op": "remove_data_source", "id": ...}
    {"op": "set_page_name",      "name": ...}

apply_patches(schema, patches) -> (新 schema, 错误列表)：
    错误列表非空时调用方不要使用结果（原 schema 不受影响，内部深拷贝）。
    所有操作应用完后还会跑一遍 validate_schema，兜住「删了数据源但事件还引用」这类问题。
"""

import copy

from lowcode_schema import validate_schema

MAX_PATCHES = 50


def _find(root: dict, node_id: str) -> dict | None:
    if root.get("id") == node_id:
        return root
    for child in root.get("children") or []:
        hit = _find(child, node_id)
        if hit:
            return hit
    return None


def _find_parent(root: dict, node_id: str) -> dict | None:
    for child in root.get("children") or []:
        if child.get("id") == node_id:
            return root
        hit = _find_parent(child, node_id)
        if hit:
            return hit
    return None


def apply_patches(schema, patches) -> tuple[dict, list[str]]:
    """应用补丁，返回 (新 schema, 错误列表)。"""
    if not isinstance(patches, list) or not patches:
        return schema, ["patches 必须是非空数组"]
    if len(patches) > MAX_PATCHES:
        return schema, [f"一次最多应用 {MAX_PATCHES} 条修改"]
    if not isinstance(schema, dict) or not isinstance(schema.get("root"), dict):
        return schema, ["当前页面 schema 无效，无法应用补丁"]

    work = copy.deepcopy(schema)
    errors: list[str] = []
    for index, patch in enumerate(patches):
        _apply_one(work, patch, index, errors)
    if not errors:
        errors.extend(validate_schema(work))
    return work, errors


def _apply_one(schema: dict, patch, index: int, errors: list) -> None:
    where = f"patches[{index}]"
    if not isinstance(patch, dict):
        errors.append(f"{where}: 必须是对象")
        return
    handler = _HANDLERS.get(patch.get("op"))
    if handler is None:
        errors.append(
            f"{where}: 不支持的操作 {patch.get('op')!r}（可用：{'、'.join(sorted(_HANDLERS))}）"
        )
        return
    handler(schema, patch, where, errors)


def _node(schema: dict, patch: dict, where: str, errors: list) -> dict | None:
    node_id = patch.get("nodeId")
    if not isinstance(node_id, str) or not node_id:
        errors.append(f"{where}: 缺少 nodeId")
        return None
    node = _find(schema["root"], node_id)
    if node is None:
        errors.append(f"{where}: 节点不存在（{node_id}）")
        return None
    return node


def _op_update_props(schema, patch, where, errors) -> None:
    node = _node(schema, patch, where, errors)
    if node is None:
        return
    props = patch.get("props")
    if not isinstance(props, dict) or not props:
        errors.append(f"{where}: props 必须是非空对象")
        return
    target = node.setdefault("props", {})
    for key, value in props.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value


def _op_set_visible(schema, patch, where, errors) -> None:
    node = _node(schema, patch, where, errors)
    if node is None:
        return
    visible = patch.get("visible")
    if visible is None:
        node.pop("visible", None)
    elif isinstance(visible, str) and visible.strip():
        node["visible"] = visible
    else:
        errors.append(f"{where}: visible 必须是字符串表达式或 null")


def _op_set_event(schema, patch, where, errors) -> None:
    node = _node(schema, patch, where, errors)
    if node is None:
        return
    event = patch.get("event")
    if not isinstance(event, str) or not event:
        errors.append(f"{where}: event 必填（如 onClick）")
        return
    action = patch.get("action")
    events = node.setdefault("events", {})
    if action is None:
        events.pop(event, None)
    elif isinstance(action, dict):
        events[event] = action
    else:
        errors.append(f"{where}: action 必须是对象或 null")
    if not events:
        node.pop("events", None)


def _op_add_node(schema, patch, where, errors) -> None:
    node = patch.get("node")
    if not isinstance(node, dict) or not node.get("id") or not node.get("type"):
        errors.append(f"{where}: node 必须是带 id 和 type 的完整组件节点")
        return
    if _find(schema["root"], node["id"]):
        errors.append(f"{where}: 节点 id 重复（{node['id']}），请换一个新 id")
        return
    parent_id = patch.get("parentId")
    if not isinstance(parent_id, str) or not parent_id:
        parent_id = schema["root"].get("id")
    parent = _find(schema["root"], parent_id)
    if parent is None:
        errors.append(f"{where}: 父节点不存在（{parent_id}）")
        return
    children = parent.setdefault("children", [])
    index = patch.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(children):
        index = len(children)
    children.insert(index, node)


def _op_remove_node(schema, patch, where, errors) -> None:
    node_id = patch.get("nodeId")
    if node_id == schema["root"].get("id"):
        errors.append(f"{where}: 不能删除根节点")
        return
    if _node(schema, patch, where, errors) is None:
        return
    parent = _find_parent(schema["root"], node_id)
    if parent is None:
        errors.append(f"{where}: 找不到节点 {node_id} 的父节点")
        return
    parent["children"] = [c for c in parent.get("children") or [] if c.get("id") != node_id]


def _op_move_node(schema, patch, where, errors) -> None:
    node_id = patch.get("nodeId")
    parent_id = patch.get("parentId")
    if node_id == schema["root"].get("id"):
        errors.append(f"{where}: 不能移动根节点")
        return
    node = _node(schema, patch, where, errors)
    if node is None:
        return
    if not isinstance(parent_id, str) or not parent_id:
        errors.append(f"{where}: 缺少 parentId")
        return
    new_parent = _find(schema["root"], parent_id)
    if new_parent is None:
        errors.append(f"{where}: 目标父节点不存在（{parent_id}）")
        return
    if _find(node, parent_id) is not None:
        errors.append(f"{where}: 不能把节点移动到它自己的子树里")
        return
    old_parent = _find_parent(schema["root"], node_id)
    if old_parent is None:
        errors.append(f"{where}: 找不到节点 {node_id} 的父节点")
        return
    old_parent["children"] = [c for c in old_parent.get("children") or [] if c.get("id") != node_id]
    children = new_parent.setdefault("children", [])
    index = patch.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(children):
        index = len(children)
    children.insert(index, node)


def _op_set_state(schema, patch, where, errors) -> None:
    key = patch.get("key")
    if not isinstance(key, str) or not key:
        errors.append(f"{where}: key 必填")
        return
    state = schema.setdefault("state", {})
    if "value" in patch and patch["value"] is not None:
        state[key] = patch["value"]
    else:
        state.pop(key, None)


def _op_upsert_data_source(schema, patch, where, errors) -> None:
    source = patch.get("dataSource")
    if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"]:
        errors.append(f"{where}: dataSource 必须是带字符串 id 的对象")
        return
    sources = schema.setdefault("dataSources", [])
    for i, item in enumerate(sources):
        if item.get("id") == source["id"]:
            sources[i] = {**item, **source}
            return
    sources.append(source)


def _op_remove_data_source(schema, patch, where, errors) -> None:
    source_id = patch.get("id")
    if not isinstance(source_id, str) or not source_id:
        errors.append(f"{where}: id 必填")
        return
    schema["dataSources"] = [
        item for item in schema.get("dataSources") or [] if item.get("id") != source_id
    ]


def _op_set_page_name(schema, patch, where, errors) -> None:
    name = patch.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{where}: name 必填")
        return
    schema["name"] = name.strip()


_HANDLERS = {
    "update_props": _op_update_props,
    "set_visible": _op_set_visible,
    "set_event": _op_set_event,
    "add_node": _op_add_node,
    "remove_node": _op_remove_node,
    "move_node": _op_move_node,
    "set_state": _op_set_state,
    "upsert_data_source": _op_upsert_data_source,
    "remove_data_source": _op_remove_data_source,
    "set_page_name": _op_set_page_name,
}
