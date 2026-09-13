"""注册给模型的工具。

业务数据都在 MySQL（读写见 tool_store.py），这里只负责参数处理与措辞。
注意：`@tool` 的 docstring 就是给模型看的工具说明，会进 prompt，
调试用的注释请写成 `#`，别留在 docstring 里。
"""

import math
from datetime import datetime, timedelta

from langchain_core.tools import tool

import tool_store


@tool
def get_weather(city: str) -> str:
    """获取指定城市的实时天气信息

    支持中国主要城市的天气查询

    Args:
        city: 城市名称，如"北京"、"上海"、"深圳"等

    Returns:
        包含温度、天气状况、空气质量的详细信息

    Examples:
        get_weather("北京") 返回 "多云，15-22℃，空气质量良"
    """
    # 天气数据在 MySQL 的 tool_weather 表，代码里不再写死
    return tool_store.find_weather(city) or f"抱歉，暂不支持查询 {city} 的天气信息。"


@tool
def calculator(expression: str) -> str:
    """计算数学表达式的结果

    支持基本运算符（+、-、*、/、**）和常用数学函数

    Args:
        expression: 数学表达式，可以包含：
            - 基本运算：2 + 3, 10 * 5, 100 / 4
            - 幂运算：2 ** 10
            - 函数：sqrt(16), abs(-5), pow(2, 3)


    Returns:
        计算结果或错误信息

    Examples:
        calculator("2 + 3 * (4 - 1)") 返回 "11"
    """
    try:
        safe_functions = {
            "sqrt": math.sqrt,
            "pow": pow,
            "abs": abs,
            "round": round,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "log": math.log,
            "pi": math.pi,
            "e": math.e,
        }
        # 使用 eval 计算表达式，注意安全性
        result = eval(expression, {"__builtins__": None}, safe_functions)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算出错：{str(e)}\n提示：请检查表达式格式，支持的函数有 sqrt,abs, pow, sin, cos, tan, log"


@tool
def get_time_info(query_type: str = "current") -> str:
    """获取时间相关信息

    Args:

        query_type: 查询类型
            - "current": 当前时间
            - "date": 今天日期
            - "tomorrow": 明天日期
            - "yesterday": 昨天日期
            - "weekday": 星期几

    Returns:
        时间信息字符串

    Examples:
        get_time_info("current") 返回 "2025年1月25日 14:30:25"
        get_time_info("weekday") 返回 "星期六"
    """
    # 注意：原来写的是 datetime.now()，但 datetime 是模块名，
    # 模块里没有 now() 这个函数，会报 AttributeError。
    now = datetime.now()
    if query_type == "current":
        return now.strftime("当前时间：%Y年%m月%d日 %H:%M:%S")
    elif query_type == "date":
        return now.strftime("今天是：%Y年%m月%d日")
    elif query_type == "tomorrow":
        tomorrow = now + timedelta(days=1)
        return tomorrow.strftime("明天是：%Y年%m月%d日")
    elif query_type == "yesterday":
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("昨天是：%Y年%m月%d日")
    elif query_type == "weekday":
        weekdays = [
            "星期一",
            "星期二",
            "星期三",
            "星期四",
            "星期五",
            "星期六",
            "星期日",
        ]
        return f"今天是{weekdays[now.weekday()]}"
    else:
        return f"不支持的查询类型：{query_type}。支持：current, date, tomorrow,yesterday, weekday"


@tool
def convert_currency(amount: float, from_curr: str, to_curr: str) -> str:
    """将指定金额从一种货币转换为另一种货币。

    Args:
        amount: 金额数值，例如 100.0。
        from_curr: 源货币代码（CNY/USD/EUR/GBP/JPY/HKD）。
        to_curr: 目标货币代码（CNY/USD/EUR/GBP/JPY/HKD）。

    Returns:
        转换后的金额字符串，例如 "100.0 USD = 720.0 CNY"

    Examples:
        convert_currency(100, "CNY", "USD") 返回 "100 CNY = 14.00 USD"

    """

    # 汇率与货币名在 MySQL 的 tool_currency 表，代码里不再写死
    from_curr = from_curr.upper()
    to_curr = to_curr.upper()

    currencies = tool_store.find_currencies([from_curr, to_curr])

    # 逐个检查，报错时指出第一个不认识的代码（和原来的行为一致）
    unknown = [code for code in (from_curr, to_curr) if code not in currencies]
    if unknown:
        supported = "/".join(tool_store.list_currency_codes())
        return f"不支持的货币类型：{unknown[0]}。支持：{supported}"

    # 转换逻辑：先转为 CNY，再转为目标货币
    amount_in_cny = amount / currencies[from_curr]["rate_to_cny"]
    converted_amount = amount_in_cny * currencies[to_curr]["rate_to_cny"]

    return (
        f"{amount} {currencies[from_curr]['name']} = "
        f"{converted_amount:.2f} {currencies[to_curr]['name']}"
    )


@tool
def search_info(keyword: str, category: str = "all") -> str:
    """搜索指定关键词的相关信息。

    Args:
        keyword: 搜索关键词，例如 "手机"、"AI"。
        category: 搜索类别，可选值：
            - "all": 全部信息（默认）
            - "product": 产品信息
            - "news": 新闻资讯

    Returns:
        搜索结果摘要字符串

    Examples:
        search_info("AI", "news") 返回 "新闻信息：..."
    """

    # 产品与新闻数据在 MySQL 的 tool_product / tool_news 表，代码里不再写死
    results = []
    if category in ["all", "product"]:
        product_result = tool_store.find_product(keyword)
        results.append(f"产品信息：{product_result or '未找到相关产品信息。'}")
    if category in ["all", "news"]:
        news_result = tool_store.find_news(keyword)
        results.append(f"新闻信息：{news_result or '未找到相关新闻。'}")
    if results:
        return "\n".join(results)
    # 类别写错时别返回含糊的话，明确列出可选值，模型才知道怎么重试
    return f"不支持的搜索类别：{category}。支持：all, product, news"


# 智能助手用到的全部工具；新增工具时只改这一处
ASSISTANT_TOOLS = [get_weather, calculator, get_time_info, convert_currency, search_info]
