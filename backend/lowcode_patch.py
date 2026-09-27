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

调用链：lowcode_agent.py 拿到模型产出的 patches -> apply_patches 在深拷贝上应用 ->
校验通过才返回新 schema 给前端/落库；中途出错原 schema 完全不受影响。
"""

import copy

from lowcode_schema import validate_schema

# 单次请求补丁条数上限：防止模型一次吐出上百条操作造成误伤/超时
MAX_PATCHES = 50


def _find(root: dict, node_id: str) -> dict | None:
    """按稳定节点 id 深度优先查找节点本身；找不到返回 None。

    因为 id 全局唯一（validate_schema 保证过），命中即唯一解，找到就立刻返回。
    """
    if root.get("id") == node_id:
        return root
    for child in root.get("children") or []:
        hit = _find(child, node_id)
        if hit:
            return hit
    return None


def _find_parent(root: dict, node_id: str) -> dict | None:
    """找 node_id 所在的父节点（用来从 children 数组里摘掉它）。

    注意：root 本身没有父节点，所以在 root 这一层不会命中——
    调用方对根节点的删除/移动会在更早的校验里被拦下。
    """
    for child in root.get("children") or []:
        if child.get("id") == node_id:
            return root
        hit = _find_parent(child, node_id)
        if hit:
            return hit
    return None


def apply_patches(schema, patches) -> tuple[dict, list[str]]:
    """应用补丁，返回 (新 schema, 错误列表)。

    前置任一失败直接原样返回 (schema, 错误)；全部成功后再跑一次全量 validate_schema。
    调用方约定：errors 非空时丢弃返回的 work，原 schema 不受任何影响（全程在深拷贝上改）。
    """
    if not isinstance(patches, list) or not patches:
        return schema, ["patches 必须是非空数组"]
    if len(patches) > MAX_PATCHES:
        return schema, [f"一次最多应用 {MAX_PATCHES} 条修改"]
    if not isinstance(schema, dict) or not isinstance(schema.get("root"), dict):
        return schema, ["当前页面 schema 无效，无法应用补丁"]

    # 关键：先深拷贝再动手，保证「应用失败」是原子的——调用方手里的旧 schema 纹丝不动
    work = copy.deepcopy(schema)
    errors: list[str] = []
    # 按数组顺序逐条应用：后一条能看到前一条的结果（比如先 add_node 再 update_props 它）
    for index, patch in enumerate(patches):
        _apply_one(work, patch, index, errors)
    # 逐条操作都没报错时，再做一次权威全量校验兜底（例如删数据源后仍有事件引用它）
    if not errors:
        errors.extend(validate_schema(work))
    return work, errors


def _apply_one(schema: dict, patch, index: int, errors: list) -> None:
    """分发单条补丁：按 op 名从 _HANDLERS 找到对应处理函数执行。"""
    where = f"patches[{index}]"
    if not isinstance(patch, dict):
        errors.append(f"{where}: 必须是对象")
        return
    # 策略表分发：op 字符串 -> 处理函数。未注册的 op 直接报错并列出全部可用操作，方便模型自修
    handler = _HANDLERS.get(patch.get("op"))
    if handler is None:
        errors.append(
            f"{where}: 不支持的操作 {patch.get('op')!r}（可用：{'、'.join(sorted(_HANDLERS))}）"
        )
        return
    handler(schema, patch, where, errors)


def _node(schema: dict, patch: dict, where: str, errors: list) -> dict | None:
    """公共前置：从 patch 里取 nodeId 并在树中定位节点；失败记错误返回 None。"""
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
    """合并式更新 props：只覆盖补丁里出现的 key；值为 null 表示删除该 key。"""
    node = _node(schema, patch, where, errors)
    if node is None:
        return
    props = patch.get("props")
    if not isinstance(props, dict) or not props:
        errors.append(f"{where}: props 必须是非空对象")
        return
    target = node.setdefault("props", {})
    for key, value in props.items():
        # null 是「删除属性」的信号（区别于显式设成空串/0），与前端命令层约定一致
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value


def _op_set_visible(schema, patch, where, errors) -> None:
    """设置/清除节点的显隐表达式；visible 为 null 表示取消条件渲染。"""
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
    """设置/清除节点上某个事件名的动作；action 为 null 表示删掉该事件。"""
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
    # 删空后顺手把空的 events 字段也清掉，保持 schema 干净
    if not events:
        node.pop("events", None)


def _op_add_node(schema, patch, where, errors) -> None:
    """在父节点 children 的指定下标插入新节点；缺省插到 root 末尾。"""
    node = patch.get("node")
    # 新节点至少要有 id 和 type 两个骨架字段，否则进树后 validate 也会报错
    if not isinstance(node, dict) or not node.get("id") or not node.get("type"):
        errors.append(f"{where}: node 必须是带 id 和 type 的完整组件节点")
        return
    # 新增 id 不能与现有节点冲突（id 是全树定位键）
    if _find(schema["root"], node["id"]):
        errors.append(f"{where}: 节点 id 重复（{node['id']}），请换一个新 id")
        return
    parent_id = patch.get("parentId")
    # 没传 parentId 时默认挂到根节点下
    if not isinstance(parent_id, str) or not parent_id:
        parent_id = schema["root"].get("id")
    parent = _find(schema["root"], parent_id)
    if parent is None:
        errors.append(f"{where}: 父节点不存在（{parent_id}）")
        return
    children = parent.setdefault("children", [])
    index = patch.get("index")
    # 下标非法（含 bool——True/False 是 int 子类，必须显式排除）时退化为追加到末尾
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(children):
        index = len(children)
    children.insert(index, node)


def _op_remove_node(schema, patch, where, errors) -> None:
    """按 id 删除节点；根节点保护不允许删。"""
    node_id = patch.get("nodeId")
    # 根节点是整棵树的锚，删了就没有 root 了，直接拒绝
    if node_id == schema["root"].get("id"):
        errors.append(f"{where}: 不能删除根节点")
        return
    if _node(schema, patch, where, errors) is None:
        return
    parent = _find_parent(schema["root"], node_id)
    if parent is None:
        errors.append(f"{where}: 找不到节点 {node_id} 的父节点")
        return
    # 用列表推导重建 children，把目标节点过滤掉（不按下标，按 id 删）
    parent["children"] = [c for c in parent.get("children") or [] if c.get("id") != node_id]


def _op_move_node(schema, patch, where, errors) -> None:
    """把节点从原父节点摘下，插入新父节点的指定下标。"""
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
    # 成环保护：目标父节点不能是被移动节点自己的后代（否则会把整棵子树挪进自己肚子里）
    if _find(node, parent_id) is not None:
        errors.append(f"{where}: 不能把节点移动到它自己的子树里")
        return
    old_parent = _find_parent(schema["root"], node_id)
    if old_parent is None:
        errors.append(f"{where}: 找不到节点 {node_id} 的父节点")
        return
    # 先从旧父节点摘掉，再插入新父节点（摘插分离，避免同一节点同时出现在两处）
    old_parent["children"] = [c for c in old_parent.get("children") or [] if c.get("id") != node_id]
    children = new_parent.setdefault("children", [])
    index = patch.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(children):
        index = len(children)
    children.insert(index, node)


def _op_set_state(schema, patch, where, errors) -> None:
    """新增/更新页面状态变量；value 缺失或为 null 表示删除该变量。"""
    key = patch.get("key")
    if not isinstance(key, str) or not key:
        errors.append(f"{where}: key 必填")
        return
    state = schema.setdefault("state", {})
    # 与 update_props 同一约定：value 为 None 即删除，而非把变量设成 null
    if "value" in patch and patch["value"] is not None:
        state[key] = patch["value"]
    else:
        state.pop(key, None)


def _op_upsert_data_source(schema, patch, where, errors) -> None:
    """按 id 新增或更新数据源；已存在则按字段浅合并（补丁里没带的字段保留）。"""
    source = patch.get("dataSource")
    if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"]:
        errors.append(f"{where}: dataSource 必须是带字符串 id 的对象")
        return
    sources = schema.setdefault("dataSources", [])
    for i, item in enumerate(sources):
        if item.get("id") == source["id"]:
            # 存在则合并：旧字段打底 + 新字段覆盖，保留未改动字段
            sources[i] = {**item, **source}
            return
    sources.append(source)


def _op_remove_data_source(schema, patch, where, errors) -> None:
    """按 id 删数据源；删完后由末尾的全量 validate 兜底检查是否还有事件引用它。"""
    source_id = patch.get("id")
    if not isinstance(source_id, str) or not source_id:
        errors.append(f"{where}: id 必填")
        return
    schema["dataSources"] = [
        item for item in schema.get("dataSources") or [] if item.get("id") != source_id
    ]


def _op_set_page_name(schema, patch, where, errors) -> None:
    """直接改页面名（去空白）。"""
    name = patch.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{where}: name 必填")
        return
    schema["name"] = name.strip()


# 操作名 -> 处理函数 的注册表：新增语义化操作时在这里登记，并在模块 docstring /
# lowcode_agent.py 的系统提示词里同步说明，模型才知道怎么用。
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
