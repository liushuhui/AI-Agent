import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.agents.middleware import SummarizationMiddleware
from langchain.messages import AIMessage, HumanMessage, SystemMessage
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

messages = [
    SystemMessage("你是个非常友好的AI助手"),
    HumanMessage("你好啊，我是老王，你是谁？"),
    AIMessage("你好老王，我是小王"),
    HumanMessage("好的小王，很高兴认识你"),
    AIMessage("你高兴得太早了"),
    HumanMessage("呵呵，你什么意思"),
]



agent = create_agent(
    model=model,
    middleware=[
        SummarizationMiddleware(
            model=model,
            trigger=[
                ("tokens", 100),
                ("messages", 6),
            ],
            keep=("messages", 2),
            summary_prompt="对历史消息摘要，消息列表如下\n{messages}"
        )
    ],
)

result = agent.invoke({"messages": messages})

for msg in result["messages"]:
    rprint(msg.pretty_print())