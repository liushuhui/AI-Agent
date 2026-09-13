import os
import sys

# 直接跑本文件时 sys.path[0] 会变成 MiddleWare/ 目录，导致 import 不到项目根目录下的 AIagent。
# 这里补一次根目录，使两种运行方式都可用：
#   python -m MiddleWare.humanInTool   （推荐，从项目根目录执行）
#   python MiddleWare/humanInTool.py
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from AIagent.agents import get_time_info, model, get_weather, calculator
from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.types import Command

from rich import print as rprint

agent = create_agent(
    model=model,
    tools=[get_weather, calculator, get_time_info],
    checkpointer=InMemorySaver(),
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "get_weather": True,
                "calculator": False,
                "get_time_info": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": "获取时间中断啦",
                },
            },
            description_prefix="中断啦！！！",
        )
    ],
)

config = {"configurable": {"thread_id": "1"}}

response = agent.invoke(
    {
        "messages": [
            HumanMessage(
                content="请帮我查询今天北京的天气\n"
                "1+1=?\n"
                "查询今天是星期几\n"
                "同时做这三件事\n"
            )
        ]
    },
    config=config,
)

weather_decision = {
    "type": "edit",
    "edited_action": {
        "name": "get_weather",
        "args": {"city": "北京", "is_forcast": True},
    },
}
calculator_decision = {
    "type": "approve",
}
time_info_decision = {"type": "approve"}

decisions = {"decisions": []}

interrupts = response.get("__interrupt__", [])
action_requests = interrupts[0].value["action_requests"]

for action in action_requests:
    if action["name"] == "get_weather":
        decisions["decisions"].append(weather_decision)
    if action["name"] == "get_time_info":
        decisions["decisions"].append(time_info_decision)

if interrupts:
    resumed_response = agent.invoke(Command(resume=decisions), config=config)
    for msg in resumed_response["messages"]:
        msg.pretty_print()
