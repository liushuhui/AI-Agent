# -*- coding: utf-8 -*-
"""低代码页面 Schema 的校验 + 内置模板。

Schema 顶层结构（详见方案文档）：
    {schemaVersion, name, state, dataSources[], root}

这里只守「结构合法性」，不校验业务语义；编辑器保存和后续 AI 生成
都必须先过这一关，保证库里从来不出现坏数据。
"""

from typing import Any

ACTION_KINDS = {"callDataSource", "setState", "navigate", "notify"}
HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
MAX_NODES = 500
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
    """返回错误列表；为空表示合法。"""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema 必须是 JSON 对象"]
    if not isinstance(schema.get("schemaVersion"), str) or not schema["schemaVersion"]:
        errors.append("缺少 schemaVersion")
    if "state" in schema and not isinstance(schema["state"], dict):
        errors.append("state 必须是对象")

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

    root = schema.get("root")
    if not isinstance(root, dict):
        errors.append("缺少 root 组件树")
        return errors
    node_ids: set[str] = set()
    _walk(root, "root", ds_ids, node_ids, errors)
    if len(node_ids) > MAX_NODES:
        errors.append(f"组件数量超过上限（{MAX_NODES}）")
    return errors


def _walk(node: dict, path: str, ds_ids: set, node_ids: set, errors: list) -> None:
    """递归检查组件节点：id 唯一、type 存在、children 合法、事件动作合法。"""
    if not isinstance(node, dict):
        errors.append(f"{path}: 节点必须是对象")
        return
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        errors.append(f"{path}: 缺少 id")
    elif node_id in node_ids:
        errors.append(f"{path}: id 重复（{node_id}）")
    else:
        node_ids.add(node_id)
    if not isinstance(node.get("type"), str) or not node["type"]:
        errors.append(f"{path}: 缺少 type")
    if "visible" in node and node["visible"] is not None and not isinstance(node["visible"], str):
        errors.append(f"{path}.visible: 必须是字符串表达式")

    children = node.get("children")
    if children is not None and not isinstance(children, list):
        errors.append(f"{path}.children: 必须是数组")
    elif isinstance(children, list):
        for i, child in enumerate(children):
            _walk(child, f"{path}.children[{i}]", ds_ids, node_ids, errors)

    events = node.get("events")
    if events is not None and not isinstance(events, dict):
        errors.append(f"{path}.events: 必须是对象")
    elif isinstance(events, dict):
        for name, action in events.items():
            _check_action(action, f"{path}.events.{name}", ds_ids, errors)


def _check_action(action: Any, path: str, ds_ids: set, errors: list) -> None:
    if not isinstance(action, dict):
        errors.append(f"{path}: 动作必须是对象")
        return
    kind = action.get("kind")
    if kind not in ACTION_KINDS:
        errors.append(f"{path}: kind 必须是 {sorted(ACTION_KINDS)} 之一")
        return
    if kind == "callDataSource":
        if action.get("dataSource") not in ds_ids:
            errors.append(f"{path}: 数据源不存在（{action.get('dataSource')}）")
        for key in ("onSuccess", "onError"):
            chain = action.get(key)
            if chain is not None:
                if not isinstance(chain, list):
                    errors.append(f"{path}.{key}: 必须是数组")
                else:
                    for i, sub in enumerate(chain):
                        _check_action(sub, f"{path}.{key}[{i}]", ds_ids, errors)
    elif kind == "setState":
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
