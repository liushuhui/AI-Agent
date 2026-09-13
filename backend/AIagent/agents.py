"""兼容层：原来所有东西都写在 agents.py 里，拆包后统一从这里再导出一次，
这样 message.py / MiddleWare 下的调试脚本都不用改 import 路径。

各模块职责见同目录下的文件：
  llm.py        模型实例
  tools.py      业务工具（天气/计算/时间/汇率/搜索）
  file_tools.py 代码读写工具（工作目录内列目录/读/写/改/删）
  files.py      文件操作实现（工具与 HTTP 接口共用）
  workspace.py  当前工作目录 + 路径安全
  approval.py   人工审批配置 + 中断解析
  assistant.py  SmartAssistant（对话入口）
  manual_loop.py  不用 create_agent 的手动工具循环示例
  text.py       消息内容纯函数

运行方式（项目根目录下）：
    python -m AIagent.agents     # 推荐
    python AIagent/agents.py     # 也可，下面先补一次 sys.path
"""

import os
import sys

# 直接跑本文件时 sys.path[0] 会变成 AIagent/ 目录，导致 import 不到包自身
# 和项目根目录下的 tool_store。这里补一次根目录（包内的 __init__.py 也会补，
# 但那种方式要求本文件是「被导入」的，直接运行时不是）。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from AIagent.approval import APPROVAL_REQUIRED_TOOLS, extract_approval_actions
from AIagent.assistant import SmartAssistant
from AIagent.llm import DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, model
from AIagent.manual_loop import get_stock_price, run_manual_tool_loop, search_news
from AIagent.text import message_text
from AIagent.tools import (
    calculator,
    convert_currency,
    get_time_info,
    get_weather,
    search_info,
)

__all__ = [
    "APPROVAL_REQUIRED_TOOLS",
    "DEEPSEEK_API_BASE",
    "DEEPSEEK_API_KEY",
    "SmartAssistant",
    "calculator",
    "convert_currency",
    "extract_approval_actions",
    "get_stock_price",
    "get_time_info",
    "get_weather",
    "message_text",
    "model",
    "run_manual_tool_loop",
    "search_info",
    "search_news",
]


if __name__ == "__main__":
    import tool_store

    tool_store.init_tool_tables()  # 直接运行本文件时自己保证数据表已就绪
    run_manual_tool_loop("苹果公司今天的股价是多少？最近有什么新闻？")
