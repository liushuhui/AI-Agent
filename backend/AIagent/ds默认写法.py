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