"""人工审批（Human-in-the-Loop）的配置与中断解析。

工具调用前会「挂起」等前端确认，确认方式由 allowed_decisions 决定：
  approve → 直接执行      edit → 允许前端改参数后再执行
  reject  → 拒绝执行      respond → 由人直接代答，工具不执行

不在这里的工具会直接执行，不会打断流程；想放行某个工具，删掉对应条目即可。
用 True 当简写也可以，等价于四种决策全开。
"""

from AIagent.file_tools import delete_path, replace_in_file, write_file

APPROVAL_REQUIRED_TOOLS: dict[str, bool | dict] = {
    "get_weather": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "查询指定城市的实时天气",
    },
    "convert_currency": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "按汇率换算金额",
    },
    "search_info": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "搜索产品或新闻信息",
    },
    "get_time_info": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "查询当前日期或星期",
    },
    # 这个工具额外开放 respond：可以由人直接给出结果，工具本身不执行
    "calculator": {
        "allowed_decisions": ["approve", "edit", "reject", "respond"],
        "description": "执行数学计算（可改参数，也可由你直接给出结果）",
    },
    # ---- 会改动磁盘的文件工具：全部需要审批 ----
    # 三种都开放 edit：可以在面板里先把要写入/替换的内容改好再放行。
    write_file.name: {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "写入文件（新建或整文件覆盖）",
    },
    replace_in_file.name: {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "修改文件中的代码片段",
    },
    delete_path.name: {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": "删除文件",
    },
}


def extract_approval_actions(interrupts) -> list | None:
    """把中断对象整理成前端可直接渲染的待审批列表；没有中断就返回 None。

    interrupt.value 的结构由 HumanInTheLoopMiddleware 决定：
      {
        "action_requests": [{"name": ..., "args": {...}, "description": ...}],
        "review_configs": [{"action_name": ..., "allowed_decisions": [...]}],
      }
    两个列表按下标一一对应，这里合并成「一项一个 dict」，前端好遍历。

    注意：stream_mode="messages" 不带中断信息，调用方拿到的是
    `agent.get_state(config).tasks[*].interrupts`（invoke 则是返回值的 `__interrupt__`）。
    """
    for item in interrupts or ():
        value = getattr(item, "value", None)
        if not isinstance(value, dict):
            continue
        requests = value.get("action_requests") or []
        configs = value.get("review_configs") or []
        actions = []
        for index, request in enumerate(requests):
            allowed = ["approve", "reject"]
            if index < len(configs):
                allowed = configs[index].get("allowed_decisions") or allowed
            actions.append(
                {
                    "name": request.get("name"),
                    "args": request.get("args") or {},
                    "description": request.get("description") or "",
                    # 前端据此决定该显示哪几个选项
                    "allowed_decisions": allowed,
                }
            )
        return actions or None
    return None
