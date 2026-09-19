# -*- coding: utf-8 -*-
"""AI 生成链路测试（直连模型，需要可用的 DEEPSEEK_API_KEY 和网络）。

运行：cd backend && python test_lowcode_agent_gen.py
两轮：
  1) 整页生成：应返回 schema 事件；
  2) 增量修改：在上面结果基础上改需求，应走 submit_patch 返回 patch 事件。

注意：会调用真实模型（消耗 token），失败时先看报错里的 401/超时原因。
"""

import json
import sys

from lowcode_agent import generate_events
from lowcode_schema import validate_schema

# 精简物料目录（真实目录由前端从物料注册表导出后随请求发送）
MATERIALS = [
    {"type": "Card", "label": "卡片", "container": True, "props": [{"name": "title"}]},
    {
        "type": "Space",
        "label": "间距容器",
        "container": True,
        "props": [{"name": "orientation"}, {"name": "size"}],
    },
    {
        "type": "Input",
        "label": "输入框",
        "props": [{"name": "placeholder"}, {"name": "value"}],
        "events": ["onChange", "onPressEnter"],
    },
    {
        "type": "Button",
        "label": "按钮",
        "props": [{"name": "text"}, {"name": "type"}],
        "events": ["onClick"],
    },
    {
        "type": "Table",
        "label": "表格",
        "props": [
            {"name": "columns"},
            {"name": "dataSource"},
            {"name": "loading"},
            {"name": "rowKey"},
        ],
    },
    {
        "type": "StatCard",
        "label": "统计卡片",
        "props": [{"name": "title"}, {"name": "value"}, {"name": "hint"}, {"name": "color"}],
    },
    {"type": "Text", "label": "文本", "props": [{"name": "text"}]},
]

GENERATE_INSTRUCTION = (
    "做一个用户管理页：顶部一个统计卡片显示用户数量，"
    "一个搜索输入框 + 查询按钮，下面是用户表格（列：姓名、邮箱），"
    "接口用 GET /users（支持 keyword 参数）。"
)

PATCH_INSTRUCTION = "把页面名改成「客户管理」，并把统计卡片的标题改为「客户总数」。"


def run_instruction(instruction: str, current_schema):
    """跑一轮生成，返回最终 schema（schema 或 patch 事件里的结果）。"""
    result = None
    for event in generate_events(instruction, MATERIALS, current_schema):
        kind = event.get("type")
        if kind == "text":
            print(event["content"], end="", flush=True)
        elif kind == "schema":
            result = event["schema"]
            print(f"\n[schema] 收到整页 schema（{len(json.dumps(result, ensure_ascii=False))} 字节）")
        elif kind == "patch":
            result = event["schema"]
            print(f"\n[patch] 收到 {event['count']} 条操作：{json.dumps(event['patches'], ensure_ascii=False)[:400]}")
        else:
            print(f"\n[{kind}] {event.get('text') or event.get('message')}")
    return result


def main() -> int:
    print("=== 第一轮：整页生成 ===")
    schema = run_instruction(GENERATE_INSTRUCTION, None)
    if schema is None:
        print("失败：没有拿到整页 schema")
        return 1
    errors = validate_schema(schema)
    if errors:
        print("失败：schema 校验不过：", errors)
        return 1
    print("\n通过：整页生成校验无误")

    print("\n=== 第二轮：增量修改（应走 submit_patch）===")
    patched = run_instruction(PATCH_INSTRUCTION, schema)
    if patched is None:
        print("失败：没有拿到修改结果")
        return 1
    errors = validate_schema(patched)
    if errors:
        print("失败：修改后的 schema 校验不过：", errors)
        return 1
    print(f"\n页面名：{schema.get('name')} -> {patched.get('name')}")
    print("\n通过：增量修改已应用且校验无误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
