"""RAG 预处理（文档加载 + 文本切分）教学示例，未接入主业务。

只演示 RAG 流水线的第一步：把磁盘上的文本读成 Document，再用
CharacterTextSplitter 切成固定大小的 chunk。切完只打印块，
**没有接向量库（Milvus）、也没有做 embedding/检索**——
本仓库的 RAG 尚未落地为业务功能，这里仅是切块原理的对照实验。

前置：backend/assets/test.txt 存在（脚本用 __file__ 反推路径，不依赖 cwd）。
"""

from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import CharacterTextSplitter

# 用脚本自身位置推导绝对路径，避免相对路径受运行时工作目录（cwd）影响
txt_path = Path(__file__).resolve().parent.parent / "assets" / "test.txt"

text = """
    LangChain 是一个用于开发由语言模型驱动的应用程序的框架的。它提供了一套工具和抽象，使开发者
    能够更容易地构建复杂的应用程序。
"""
text_loader = TextLoader(file_path=txt_path, encoding="utf-8")
docs = text_loader.load()
# print(docs)

splitter = CharacterTextSplitter(
    chunk_size=50,  # 每块大小
    chunk_overlap=5,  # 块与块之间的重复字符数
    # length_function=len,
    separator="",  # 设置为空字符串时，表示禁用分隔符优先
)

texts = splitter.split_text(text)
for i, chunk in enumerate(texts):
    print(f"块 {i+1}:长度：{len(chunk)}")
    print(chunk)
    print("-" * 50)
