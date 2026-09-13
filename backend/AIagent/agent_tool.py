import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from rich import print as rprint

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")
# 使用langchain统一初始化模型
model = init_chat_model(
    # model="deepseek-flash",
    # model_provider="deepseek",
    model="deepseek:deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_API_BASE,
)


# docstring 就是给模型看的「工具说明书」，必须和工具实际能力一致且保持简洁。
@tool(parse_docstring=True)
def get_stock_price(company: str, time_period: str = "today") -> str:
    """查询指定股票的当前价格。

    Args:
        company: 公司名称，例如「Apple」。
        time_period: 时间周期，例如「today」。
    """
    mock_data = {
        "苹果公司": {"today": 185.20, "week": 183.50, "month": 180.75},
        "微软公司": {"today": 415.86, "week": 412.30, "month": 405.42},
        "谷歌公司": {"today": 15.42, "week": 15.20, "month": 14.85},
    }

    if company in mock_data:
        price = mock_data[company].get(
            time_period, "暂无相关信息"
        )  # 模拟返回第一条新闻作为价格信息
        return f"当前{company}的股价信息（{time_period}）：{price}"
    else:
        return f"抱歉，未找到{company}的股价信息。"


@tool(parse_docstring=True)
def search_news(company: str) -> str:
    """搜索指定公司的财经新闻。

    Args:
        company: 公司名称

    Returns:
        公司的财经新闻，每个新闻占一行
    """
    # 模拟新闻数据
    mock_news = {
        "苹果公司": [
            "苹果发布新款iPhone，股价上涨3%",
            "苹果与欧盟达成反垄断和解协议",
            "苹果将在印度扩大生产规模",
        ],
        "微软公司": [
            "微软Azure云业务季度增长超预期",
            "微软完成对Nuance的收购",
            "微软推出新一代AI助手Copilot",
        ],
        "谷歌公司": [
            "谷歌发布新AI模型，性能提升20%",
            "谷歌与OpenAI合作，开发新的AI助手",
            "谷歌在欧洲展开AI研究项目",
        ],
    }
    news_list = mock_news.get(company, [f"未找到{company}的相关新闻"])
    return "\n".join(news_list)


messages = []

# 工具清单：注册给模型和执行时查找共用同一份，避免两边写重复导致对不上
tools = [get_stock_price, search_news]

# 工具名 -> 工具对象，执行时直接查表，新增工具无需再改判断逻辑
tools_by_name = {tool.name: tool for tool in tools}

model_with_tool = model.bind_tools(tools)

human_message = HumanMessage(content="苹果公司今天的股价是多少？最近有什么新闻？")
messages.append(human_message)

while True:
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
else:
    rprint("[yellow]未得到最终回答（末尾不是 AI 消息）。[/yellow]")
