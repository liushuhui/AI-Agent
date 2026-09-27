"""DeepSeek 官方 SDK 直连的最小示例（教学用，未接入主链路）。

这是「最原始」的接法：直接用 langchain_deepseek.ChatDeepSeek 连 DeepSeek，
没有工具、没有中间件、没有会话管理——只验证模型能不能通。
主业务统一走 AIagent/llm.py 的 init_chat_model（带 timeout / max_tokens / 降级），
这里只是对照看 SDK 原生长什么样。

前置：backend/.env 里配好 DEEPSEEK_API_KEY / DEEPSEEK_API_BASE，并能联网。
"""

import os
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv(override=True)  # 覆盖系统环境变量，优先使用 .env 文件中的配置

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")

llm_ds = ChatDeepSeek(
    model="deepseek-flash",
    # api_key=DEEPSEEK_API_KEY,
    # base_url=DEEPSEEK_API_BASE,
)

response = llm_ds.invoke("你是谁")

print(response)