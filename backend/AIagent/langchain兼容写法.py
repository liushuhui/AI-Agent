"""用通用 ChatOpenAI 接 DeepSeek 的示例（教学用，未接入主链路）。

DeepSeek 提供 OpenAI 兼容的 /v1 端点，所以不必用专用 SDK，
直接拿 langchain_openai.ChatOpenAI 把 base_url 指过去即可——
和「ds默认写法.py」是等价的两种接法，这里演示兼容性写法。

前置：backend/.env 里配好 DEEPSEEK_API_KEY / DEEPSEEK_API_BASE，并能联网。
"""

import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv(override=True) 
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE")

llm_ds = ChatOpenAI(
    model='deepseek-flash',
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_API_BASE
)

response = llm_ds.invoke("1+1等于多少")
print(response)