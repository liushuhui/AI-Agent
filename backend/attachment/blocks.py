"""把「用户输入文本 + 附件」组装成模型要的 content。

  - 图片 → OpenAI 兼容的多模态 content block：{"type": "image_url", ...}
  - 文档/表格/文本 → 解析好的正文，作为 text block 注入
"""

import base64
import os

from attachment.config import (
    KIND_LABELS,
    MAX_ATTACHMENT_TEXT,
    MAX_ATTACHMENT_TEXT_TOTAL,
    MAX_IMAGE_BYTES,
    MODEL_VISION,
)
from attachment.store import storage_path


def _image_data_url(record: dict) -> str | None:
    """把磁盘上的图片读成 data URL（OpenAI 兼容接口最通用的传图方式）。"""
    path = storage_path(record)
    if not os.path.isfile(path):
        return None
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    mime = record.get("mime") or "image/png"
    return f"data:{mime};base64,{encoded}"


def _image_block(record: dict, include_image: bool) -> list:
    name = record.get("name") or "图片"
    if include_image and MODEL_VISION:
        url = _image_data_url(record)
        if url:
            return [
                {"type": "text", "text": f"【图片附件：{name}】"},
                {"type": "image_url", "image_url": {"url": url}},
            ]
        note = "（图片文件缺失或过大，未能读取）"
    elif not MODEL_VISION:
        note = "（当前模型不支持读取图片，只能看到文件名；如需分析请让用户描述图片内容）"
    else:
        note = "（历史图片，内容不再重复发送）"
    return [{"type": "text", "text": f"【图片附件：{name}】{note}"}]


def _text_block(record: dict, remaining: int) -> tuple:
    """把解析出的正文做成 text block，返回 (block, 剩余预算)。"""
    name = record.get("name") or "附件"
    label = KIND_LABELS.get(record.get("kind") or "other", "文件")
    header = f"【附件：{name}（{label}）】"

    if record.get("status") != "ready":
        body = f"（解析失败：{record.get('error') or '未知原因'}）"
    else:
        content = (record.get("text_content") or "").strip()
        if not content:
            body = "（未解析出可用文本）"
        else:
            limit = max(0, min(MAX_ATTACHMENT_TEXT, remaining))
            body = content[:limit]
            if len(content) > limit:
                body += f"\n……（内容较长，此处仅提供前 {limit} 字）"
            remaining -= len(body)
    return {"type": "text", "text": f"{header}\n{body}"}, remaining


def build_content_blocks(text: str, records: list, include_images: bool = True):
    """把「用户输入文本 + 附件」组装成模型需要的 content。

    没有附件时返回纯字符串（普通文本模型最省事的形态）；
    有图片时返回 content block 列表（多模态形态）。
    """
    blocks = []
    if text and text.strip():
        blocks.append({"type": "text", "text": text.strip()})

    remaining = MAX_ATTACHMENT_TEXT_TOTAL
    for record in records or []:
        if not record:
            continue
        if record.get("kind") == "image":
            blocks.extend(_image_block(record, include_images))
        else:
            block, remaining = _text_block(record, remaining)
            blocks.append(block)

    if not blocks:
        return text or ""
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return blocks[0]["text"]
    return blocks
