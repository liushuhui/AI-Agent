"""HumanInTheLoopMiddleware（人工审批 / HITL）参考示例。

MiddleWare 包下的教学脚本：单独演示「模型要调敏感工具时先中断、把待办动作
交给人决定（批准/拒绝/改参数），再用 Command(resume=...) 续跑」这一机制。
它对应主业务 AIagent/assistant.py 中间件栈第 8 层的人工审批——
那里把审批接进 Web 端让用户点按钮，这里把决策写死在脚本里跑通流程。

前置：backend/.env 配好 DEEPSEEK_API_KEY；工具 get_weather/calculator/get_time_info
来自 AIagent.agents（内部读 tool_store 数据表），无需联网。
"""

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

# checkpointer 记下「图运行状态」，被中断的工具调用挂在这里，靠 thread_id 才能续跑。
agent = create_agent(
    model=model,
    tools=[get_weather, calculator, get_time_info],
    checkpointer=InMemorySaver(),
    middleware=[
        HumanInTheLoopMiddleware(
            # interrupt_on 三种写法：True=每次都中断等人确认；False=直接放行不中断；
            # dict=自定义允许的决策项与提示语（这里 get_time_info 走自定义决策）。
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

# 第一轮 invoke 会在第一个需要审批的工具处中断，返回 __interrupt__；
# 这里按动作名拼好每一个 decision，再用 Command(resume=...) 把结果喂回去续跑。
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
