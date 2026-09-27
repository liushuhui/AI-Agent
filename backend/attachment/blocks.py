"""把「用户输入文本 + 附件」组装成模型要的 content。

  - 图片 → OpenAI 兼容的多模态 content block：{"type": "image_url", ...}
  - 文档/表格/文本 → 解析好的正文，作为 text block 注入

这是「两步式」的第二步（第一步在 store.py 上传落库）：消息历史里只带附件 id，
真正发给模型前在这里按 id 取出记录，拼成 LangChain HumanMessage 的 content。
token 控制策略集中在两处：
  - 图片：只把最近 IMAGE_KEEP_TURNS 轮的图片真发（include_images=True），
    更早的图片降级成一行文字占位（由调用方 message.py 决定 include_images）；
  - 文本：单个附件与整条消息都有字符预算上限，见 _text_block。
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
    """把磁盘上的图片读成 data URL（OpenAI 兼容接口最通用的传图方式）。

    形如 data:<mime>;base64,<编码>，直接内嵌进请求，无需模型服务端再回源拉取。
    文件缺失或过大时返回 None，由上层决定降级成文字占位。
    """
    path = storage_path(record)
    if not os.path.isfile(path):
        return None
    size = os.path.getsize(path)
    # 二次保险：上传时压过一次，这里再防一道——超过内联上限就不发，避免撑爆请求体。
    if size > MAX_IMAGE_BYTES:
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    mime = record.get("mime") or "image/png"
    return f"data:{mime};base64,{encoded}"


def _image_block(record: dict, include_image: bool) -> list:
    """为一张附件生成 content block 列表。

    include_image 由调用方根据「是否在最近 IMAGE_KEEP_TURNS 轮内」决定：
      - True 且模型支持视觉 → 真图 base64 内联；
      - False（历史图片）→ 只留一行文字占位，省 token；
      - 模型不支持视觉 → 告诉模型只能看到文件名；
      - 文件缺失/过大 → 说明没能读取。
    无论哪种情况，前面都带一行中文标题，让模型知道这是「哪张图」。
    """
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
    """把解析出的正文做成 text block，返回 (block, 剩余预算)。

    remaining 是「本条消息所有文本附件还能占用多少字符」的全局预算
    （初始 MAX_ATTACHMENT_TEXT_TOTAL）。单附件再叠一层 MAX_ATTACHMENT_TEXT 上限，
    两者取小；用完一个附件就从 remaining 里扣掉它实际占用的字符数，
    保证多个附件一起发时总字数不超预算。
    """
    name = record.get("name") or "附件"
    label = KIND_LABELS.get(record.get("kind") or "other", "文件")
    header = f"【附件：{name}（{label}）】"

    if record.get("status") != "ready":
        # 上传时解析失败的附件，把失败原因告诉模型，而不是静默丢空。
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

    为什么还要支持「返回 str」：LangChain 对纯文本消息直接接受字符串，
    只有当确实出现图片块时才必须用 [{type: text/image_url}, ...] 这种列表形态。
    """
    blocks = []
    if text and text.strip():
        blocks.append({"type": "text", "text": text.strip()})

    # 文本预算按「一条消息内的所有附件」共享，从总上限往下扣。
    remaining = MAX_ATTACHMENT_TEXT_TOTAL
    for record in records or []:
        if not record:
            continue
        if record.get("kind") == "image":
            # 图片块不消耗文本预算（base64 体积另由上传/压缩阶段控制）。
            blocks.extend(_image_block(record, include_images))
        else:
            block, remaining = _text_block(record, remaining)
            blocks.append(block)

    # 全空：连用户文本都没有，原样返回（多为兜底，正常调用方已在 message.py 过滤）。
    if not blocks:
        return text or ""
    # 只有一个纯文本块（通常就是用户自己打的字，没附件）→ 退回字符串，最省。
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return blocks[0]["text"]
    return blocks
