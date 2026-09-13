import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
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

class ActorModel(BaseModel):
    name: str = Field(..., description="演员姓名")
    age: int = Field(..., description="演员年龄")
    gender: str = Field(..., description="演员性别")

class MovieModel(BaseModel):
    title: str = Field(..., description="电影标题")
    director: str = Field(..., description="导演")
    year: int = Field(..., description="上映年份")
    genre: str = Field(..., description="电影类型")
    rating: float = Field(..., description="评分")
    actor: ActorModel = Field(..., description="主演信息")


# 思考模式（thinking mode）不支持强制指定 tool_choice，
# 所以不能用默认的 function_calling，改用 json_mode（走 response_format={"type":"json_object"}）
model_with_structure = model.with_structured_output(MovieModel, method="json_mode")

# DeepSeek 的 JSON 模式要求在 prompt 里出现 "json" 字样并给出格式示例，
# 否则模型可能一直输出空白直到达到 token 上限。用 PydanticOutputParser 生成格式说明。
parser = PydanticOutputParser(pydantic_object=MovieModel)
question = "请帮我推荐一部经典的科幻电影，并提供其标题、导演、上映年份、类型和评分。"
response = model_with_structure.invoke(
    f"{question}\n\n{parser.get_format_instructions()}"
)
rprint("模型输出：", response)
# response 是 MovieModel 实例，直接按字段名取值
rprint("电影标题：", response.title)
rprint("导演：", response.director)
rprint("演员名称：", response.actor.name)
# 想一次性拿到 dict，用 Pydantic 的 model_dump()
rprint("转成字典：", response.model_dump())
rprint("字典取值：", response.model_dump()["title"])
