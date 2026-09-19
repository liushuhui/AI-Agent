import os
import sys

# 直接跑本文件时 sys.path[0] 会变成 MiddleWare/ 目录，导致 import 不到 backend 下的 AIagent。
# 这里补一次根目录，使两种运行方式都可用：
#   python -m MiddleWare.memory   （推荐，从 backend 目录执行）
#   python MiddleWare/memory.py   （或在 VS Code 里点运行）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langgraph.store.memory import InMemoryStore
from langchain_core.messages import HumanMessage
from typing import NotRequired
from langchain.agents import create_agent, AgentState
from langchain.tools import tool, ToolRuntime
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model
# from AIagent.llm import model
from dotenv import load_dotenv

# 必须真正调用 load_dotenv()，否则 os.getenv 拿不到 .env 里的值（全是 None）。
# 用绝对路径指向 backend/.env，这样无论从哪个目录运行都能找到。
load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")
if not DEEPSEEK_API_KEY or not DEEPSEEK_API_BASE:
    raise RuntimeError("DEEPSEEK_API_KEY / DEEPSEEK_API_BASE 未配置，请检查 backend/.env")


store = InMemoryStore()


class CustomState(AgentState):
    user_id: NotRequired[str]


model = init_chat_model(
    model="deepseek:deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_API_BASE,
)


@tool(parse_docstring=True)
def save_user_info(name: str, runtime: ToolRuntime) -> str:
    """
    把用户的个人信息（如姓名）保存到长期记忆中。

    Args:
        name: 用户名

    Returns:
        str: 保存状态

    """
    runtime.store.put(("users",), runtime.state["user_id"], {"name": name})
    return "saved"


@tool(parse_docstring=True)
def get_user_info(runtime: ToolRuntime) -> str:
    """
    从长期记忆中读取用户信息

    Returns:
        str: 用户信息

    """

    item = runtime.store.get(("users",), runtime.state["user_id"])
    return str(item) if item else "unknown"


agent = create_agent(
    model=model,
    tools=[save_user_info, get_user_info],
    store=store,
    system_prompt="用户提及个人信息时及时记录，用户询问个人信息时尝试用工具检索",
    state_schema=CustomState,
)

response1 = agent.invoke(
    {"messages": [HumanMessage("你好，很高兴认识你，我是小花")], "user_id": "user-1"}
)
for msg in response1["messages"]:
    msg.pretty_print()

response2 = agent.invoke({"messages": [HumanMessage("我是谁")], "user_id": "user-1"})

for msg in response2["messages"]:
    msg.pretty_print()
