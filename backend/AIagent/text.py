"""消息内容相关的纯函数。"""


def message_text(msg) -> str:
    """把消息 content（可能是 str，也可能是多模态列表）统一成纯文本。"""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in content
        )
    return ""
