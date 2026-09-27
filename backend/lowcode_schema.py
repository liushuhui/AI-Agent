# -*- coding: utf-8 -*-
"""低代码页面 Schema 的校验 + 内置模板。

Schema 顶层结构（详见方案文档）：
    {schemaVersion, name, state, dataSources[], root}

这里只守「结构合法性」，不校验业务语义；编辑器保存和后续 AI 生成
都必须先过这一关，保证库里从来不出现坏数据。

架构位置：
    - 上游：前端编辑器保存、lowcode_agent.py（AI 生成/改页）、lowcode_patch.py（语义化补丁）。
    - 下游：lowcode_store.py 落库前再调一次 validate_schema 做兜底。
    - 本文件只产出「错误列表」，不抛异常、不写库，纯函数好测。

DSL 四大块：
    - state       : {变量名: 初值}，组件 props/events 里用 {{ state.x }} 引用；
    - dataSources : REST 数据源数组，事件 callDataSource 按 id 引用；
    - root        : 组件树（递归节点），每个节点靠稳定字符串 id 定位；
    - events      : 挂在节点上的动作链（可 onSuccess/onError 嵌套子链）。
"""

from typing import Any

# 事件动作允许的 kind 白名单：新增动作类型时这里要同步放行，并在 _check_action 里补字段校验
ACTION_KINDS = {"callDataSource", "setState", "navigate", "notify"}
# 数据源支持的 HTTP 方法（与前端数据源配置面板保持一致）
HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
# 单页组件节点数硬上限：防 AI 生成失控/恶意超大树拖垮校验与渲染
MAX_NODES = 500
# 单个 Schema 序列化后的字节上限（512KB）：防止单行 LONGTEXT 被塞爆、也防 SSE 下发过大
MAX_SCHEMA_BYTES = 512 * 1024


def default_schema(name: str) -> dict:
    """新建页面的默认 Schema：一个带提示文案的空卡片。"""
    return {
        "schemaVersion": "1.0",
        "name": name,
        "state": {},
        "dataSources": [],
        "root": {
            "id": "root",
            "type": "Card",
            "props": {"title": name},
            "children": [
                {
                    "id": "hint",
                    "type": "Text",
                    "props": {"text": "把左侧物料拖进画布，或选中组件后在右侧改属性。"},
                }
            ],
        },
    }


def sample_schema() -> dict:
    """内置示例页面：直接绑定现成的 GET /users 接口，开箱即可跑通数据链路。"""
    return {
        "schemaVersion": "1.0",
        "name": "用户管理（示例）",
        "state": {"keyword": ""},
        "dataSources": [
            {
                "id": "userList",
                "kind": "rest",
                "method": "GET",
                "url": "/users",
                "params": {"keyword": "{{ state.keyword }}"},
                "autoLoad": True,
            }
        ],
        "root": {
            "id": "root",
            "type": "Card",
            "props": {"title": "用户管理（示例页面）"},
            "children": [
                {
                    "id": "bar",
                    "type": "Space",
                    "props": {"orientation": "horizontal"},
                    "children": [
                        {
                            "id": "kw",
                            "type": "Input",
                            "props": {
                                "placeholder": "搜索姓名/邮箱",
                                "allowClear": True,
                                "style": {"width": 260},
                                "value": "{{ state.keyword }}",
                            },
                            "events": {
                                "onChange": {
                                    "kind": "setState",
                                    "key": "keyword",
                                    "value": "{{ event.value }}",
                                },
                                "onPressEnter": {
                                    "kind": "callDataSource",
                                    "dataSource": "userList",
                                },
                            },
                        },
                        {
                            "id": "btn",
                            "type": "Button",
                            "props": {"text": "查询", "type": "primary"},
                            "events": {
                                "onClick": {
                                    "kind": "callDataSource",
                                    "dataSource": "userList",
                                }
                            },
                        },
                    ],
                },
                {
                    "id": "table",
                    "type": "Table",
                    "props": {
                        "rowKey": "id",
                        "dataSource": "{{ dataSources.userList.data }}",
                        "loading": "{{ dataSources.userList.loading }}",
                        "columns": [
                            {"key": "name", "title": "姓名", "dataIndex": "name"},
                            {"key": "email", "title": "邮箱", "dataIndex": "email"},
                            {
                                "key": "created_at",
                                "title": "创建时间",
                                "dataIndex": "created_at",
                            },
                        ],
                    },
                },
            ],
        },
    }


def validate_schema(schema: Any) -> list[str]:
    """返回错误列表；为空表示合法。

    校验顺序：顶层字段 -> dataSources 数据源（收集合法 id 供引用完整性检查）
    -> root 组件树递归 -> 节点总数上限。
    故意收集全部错误而非遇错即返：AI 一次能看到所有问题，减少多轮自检次数。
    """
    errors: list[str] = []
    # 顶层必须是 JSON 对象；连类型都不对就没必要继续往下走，直接短路
    if not isinstance(schema, dict):
        return ["schema 必须是 JSON 对象"]
    if not isinstance(schema.get("schemaVersion"), str) or not schema["schemaVersion"]:
        errors.append("缺少 schemaVersion")
    # state 可选，给了就必须是对象（变量名->初值的字典）
    if "state" in schema and not isinstance(schema["state"], dict):
        errors.append("state 必须是对象")

    # ---- 数据源段：边校验边把合法 ds_id 收进集合，后面 events 引用它做存在性检查 ----
    ds_ids: set[str] = set()
    sources = schema.get("dataSources") or []
    if not isinstance(sources, list):
        errors.append("dataSources 必须是数组")
    else:
        for i, ds in enumerate(sources):
            where = f"dataSources[{i}]"
            if not isinstance(ds, dict):
                errors.append(f"{where}: 必须是对象")
                continue
            if ds.get("kind", "rest") != "rest":
                errors.append(f"{where}: 目前只支持 rest 数据源")
            ds_id = ds.get("id")
            if not isinstance(ds_id, str) or not ds_id:
                errors.append(f"{where}: 缺少 id")
            elif ds_id in ds_ids:
                errors.append(f"{where}: id 重复（{ds_id}）")
            else:
                ds_ids.add(ds_id)
            if not isinstance(ds.get("url"), str) or not ds["url"].strip():
                errors.append(f"{where}: 缺少 url")
            if ds.get("method") not in HTTP_METHODS:
                errors.append(f"{where}: method 必须是 {sorted(HTTP_METHODS)} 之一")

    # ---- 组件树段：root 缺失就没法递归，直接返回（不再有意义收集后续错误）----
    root = schema.get("root")
    if not isinstance(root, dict):
        errors.append("缺少 root 组件树")
        return errors
    # node_ids 在递归中累积：用于跨整棵树查重 + 最后统计节点总数
    node_ids: set[str] = set()
    _walk(root, "root", ds_ids, node_ids, errors)
    if len(node_ids) > MAX_NODES:
        errors.append(f"组件数量超过上限（{MAX_NODES}）")
    return errors


def _walk(node: dict, path: str, ds_ids: set, node_ids: set, errors: list) -> None:
    """递归检查组件节点：id 唯一、type 存在、children 合法、事件动作合法。

    path 是给报错人看的定位串（如 root.children[2].events.onClick），
    让前端/AI 能直接定位到出问题的节点。
    """
    if not isinstance(node, dict):
        errors.append(f"{path}: 节点必须是对象")
        return
    # 每个节点必须有全局唯一字符串 id：这是语义化补丁 lowcode_patch.py 的定位基础
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        errors.append(f"{path}: 缺少 id")
    elif node_id in node_ids:
        errors.append(f"{path}: id 重复（{node_id}）")
    else:
        node_ids.add(node_id)
    # type 必须命中物料目录里某个组件；这里不校验「物料是否存在」（那是物料目录的事），
    # 只保证非空字符串，避免前端渲染时拿到空 type 崩掉
    if not isinstance(node.get("type"), str) or not node["type"]:
        errors.append(f"{path}: 缺少 type")
    # visible 是可选的显隐表达式；给了就必须是 {{ ... }} 字符串，不允许裸 bool
    if "visible" in node and node["visible"] is not None and not isinstance(node["visible"], str):
        errors.append(f"{path}.visible: 必须是字符串表达式")

    # children 可选；给了就必须是数组，再递归下钻
    children = node.get("children")
    if children is not None and not isinstance(children, list):
        errors.append(f"{path}.children: 必须是数组")
    elif isinstance(children, list):
        for i, child in enumerate(children):
            _walk(child, f"{path}.children[{i}]", ds_ids, node_ids, errors)

    # events 是 {事件名: 动作} 的映射；逐事件交给 _check_action 校验
    events = node.get("events")
    if events is not None and not isinstance(events, dict):
        errors.append(f"{path}.events: 必须是对象")
    elif isinstance(events, dict):
        for name, action in events.items():
            _check_action(action, f"{path}.events.{name}", ds_ids, errors)


def _check_action(action: Any, path: str, ds_ids: set, errors: list) -> None:
    """校验单个事件动作对象。

    callDataSource 可带 onSuccess/onError 两条「动作链」（数组），
    链里的每个子动作又递归走本函数——这就是事件动作链的嵌套结构。
    """
    if not isinstance(action, dict):
        errors.append(f"{path}: 动作必须是对象")
        return
    kind = action.get("kind")
    if kind not in ACTION_KINDS:
        errors.append(f"{path}: kind 必须是 {sorted(ACTION_KINDS)} 之一")
        return
    if kind == "callDataSource":
        # 引用完整性：动作要调的数据源必须在 dataSources 里真实存在（这是最容易被 AI 写错的点）
        if action.get("dataSource") not in ds_ids:
            errors.append(f"{path}: 数据源不存在（{action.get('dataSource')}）")
        # 成功/失败后的后续动作链：可选，给了就必须是数组并递归校验
        for key in ("onSuccess", "onError"):
            chain = action.get(key)
            if chain is not None:
                if not isinstance(chain, list):
                    errors.append(f"{path}.{key}: 必须是数组")
                else:
                    for i, sub in enumerate(chain):
                        _check_action(sub, f"{path}.{key}[{i}]", ds_ids, errors)
    elif kind == "setState":
        # 写状态：必须指定变量 key 和 value（value 允许是 {{ }} 表达式）
        if not isinstance(action.get("key"), str) or not action["key"]:
            errors.append(f"{path}: setState 必须带字符串 key（写成 {{\"kind\": \"setState\", \"key\": \"x\", \"value\": ...}}）")
        if "value" not in action:
            errors.append(f"{path}: setState 缺少 value")
    elif kind == "navigate":
        if not isinstance(action.get("to"), str) or not action["to"]:
            errors.append(f"{path}: navigate 必须带字符串 to（跳转路径）")
    elif kind == "notify":
        if not isinstance(action.get("text"), str) or not action["text"]:
            errors.append(f"{path}: notify 必须带字符串 text（提示文案）")
