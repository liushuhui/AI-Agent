"""消息内容相关的纯函数。

LangChain 消息的 content 形态不统一（纯字符串，或多模态块组成的列表），
这里集中一个出口把它压成纯文本，避免各处重复判断类型。
"""


def message_text(msg) -> str:
    """把消息 content（可能是 str，也可能是多模态列表）统一成纯文本。"""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 多模态 content：元素可能是裸字符串，也可能是 {"type": "text", "text": "..."}
        # 之类的块；图片等非文本块没有 text 键就当空串跳过，只把文本片段拼起来。
        return "".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in content
        )
    return ""
