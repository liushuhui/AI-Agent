"""不依赖 create_agent 的手动工具循环示例（教学 / 调试用）。

这里刻意不接人工审批：它就是「模型 → 工具 → 模型」最小闭环的样板。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from rich import print as rprint

import tool_store
from AIagent.llm import model
from AIagent.text import message_text


@tool
def get_stock_price(company: str) -> str:
    """查询指定公司的实时股价

    Args:
        company: 公司名称，如"苹果公司"、"特斯拉"

    Returns:
        股价信息字符串

    Examples:
        get_stock_price("苹果公司") 返回 "苹果公司(AAPL) 189.30 美元"
    """
    # 行情数据在 MySQL 的 tool_stock 表，代码里不再写死
    return tool_store.find_stock(company) or f"暂未收录 {company} 的股价信息。"


@tool
def search_news(keyword: str) -> str:
    """搜索指定关键词的最新新闻

    Args:
        keyword: 搜索关键词，如"苹果公司"、"人工智能"

    Returns:
        新闻摘要字符串

    Examples:
        search_news("苹果公司") 返回 "1. 苹果发布新芯片 2. 苹果营收超预期"
    """
    # 与 search_info 的新闻共用 tool_news 表，代码里不再写死
    return tool_store.find_news(keyword) or f"未找到关于 {keyword} 的新闻。"


def run_manual_tool_loop(user_input: str, max_rounds: int = 5) -> str:
    """不依赖 create_agent，手动实现「模型 → 工具 → 模型」循环，返回最终回答。"""
    # 工具清单：注册给模型和执行时查找共用同一份，避免两边写重复导致对不上
    loop_tools = [get_stock_price, search_news]

    # 工具名 -> 工具对象，执行时直接查表，新增工具无需再改判断逻辑
    tools_by_name = {t.name: t for t in loop_tools}

    model_with_tool = model.bind_tools(loop_tools)

    messages = [HumanMessage(content=user_input)]

    # 原代码用的是 while True：模型一直要求调工具时会无限循环。
    # 这里加最大轮数兜底，超限就停下。
    # 注意：max_rounds 是防死循环的保险丝，不是终止条件，
    # 终止条件是「本轮没有 tool_calls」。
    for _ in range(max_rounds):
        response = model_with_tool.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            rprint("没有工具调用")
            break  # 没有工具调用 = 已经是最终回答
        for tool_call in response.tool_calls:
            selected_tool = tools_by_name.get(tool_call["name"])
            if selected_tool is None:
                # 模型可能给出未注册的工具名，这里也要返回一条 ToolMessage，
                # 否则 tool_calls 与 ToolMessage 对不上，下一轮请求会报错
                tool_result = ToolMessage(
                    content=f"未找到名为 {tool_call['name']} 的工具。",
                    tool_call_id=tool_call["id"],
                )
            else:
                tool_result = selected_tool.invoke(tool_call)
            rprint(f"[green]工具调用结果：{tool_result.content}[/green]")
            messages.append(tool_result)

    for message in messages:
        message.pretty_print()

    # 只有正常 break 退出时，末尾才是 AI 的最终回答；
    # 轮数耗尽退出时末尾是 ToolMessage，不能当成最终回答打印。
    last = messages[-1]
    if isinstance(last, AIMessage):
        rprint(f"最终回答：{last.content}")
        return message_text(last)
    rprint("[yellow]未得到最终回答（末尾不是 AI 消息）。[/yellow]")
    return ""
