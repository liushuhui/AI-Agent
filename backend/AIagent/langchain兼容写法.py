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