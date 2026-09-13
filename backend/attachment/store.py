"""上传落盘与对外视图。"""

import os
import uuid

import db
from attachment.config import (
    ALLOWED_EXTS,
    AttachmentError,
    KIND_BY_EXT,
    KIND_LABELS,
    MAX_IMAGE_BYTES,
    MAX_UPLOAD_BYTES,
    MIME_BY_EXT,
    UPLOAD_DIR,
    guess_mime,
)
from attachment.parsers import extract_text, shrink_image_if_needed


def storage_path(record: dict) -> str:
    """由记录推出磁盘绝对路径（stored_name 是 uuid + 白名单后缀，天然防路径穿越）。"""
    return os.path.join(UPLOAD_DIR, record["stored_name"])


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_upload(storage) -> dict:
    """校验 + 落盘 + 解析 + 落库，返回附件记录。校验失败抛 AttachmentError。"""
    raw_name = (storage.filename or "").strip()
    if not raw_name:
        raise AttachmentError("文件名为空")

    # 只取最后一段文件名，防止 ../ 之类的路径穿越
    name = os.path.basename(raw_name.replace("\\", "/")) or "未命名文件"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTS:
        raise AttachmentError(
            f"不支持的文件类型：.{ext or '未知'}（支持：{'、'.join(sorted(ALLOWED_EXTS))}）"
        )

    kind = KIND_BY_EXT[ext]
    data = storage.read()
    size = len(data)
    if size == 0:
        raise AttachmentError("文件内容为空")

    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_UPLOAD_BYTES
    if size > limit:
        raise AttachmentError(
            f"文件超过上限（{KIND_LABELS[kind]}最大 {limit / 1024 / 1024:g}MB）"
        )

    ensure_upload_dir()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, stored_name)
    with open(path, "wb") as handle:
        handle.write(data)

    mime = guess_mime(ext, storage.mimetype)

    if kind == "image":
        path, ext, size = shrink_image_if_needed(path, ext, size)
        stored_name = os.path.basename(path)
        mime = MIME_BY_EXT.get(ext, mime)

    # 解析失败不阻断上传：记录 status=failed + 原因，前端自行决定是否发送
    status, error, text_content = "ready", None, ""
    try:
        text_content = extract_text(kind, path, data)
    except AttachmentError as exc:
        status, error = "failed", str(exc)
    except Exception as exc:  # 解析库抛出的意外错误
        status, error = "failed", f"解析失败：{exc}"

    return db.create_attachment(
        name=name,
        stored_name=stored_name,
        mime=mime,
        size=size,
        kind=kind,
        status=status,
        error=error,
        text_content=text_content,
    )


def public_view(record: dict, preview_chars: int = 200) -> dict:
    """对外的附件视图：不含磁盘路径，文本只给一小段预览。"""
    text = record.get("text_content") or ""
    preview = text.strip().replace("\r", "")[:preview_chars]
    if len(text) > preview_chars:
        preview += "…"
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "kind": record.get("kind"),
        "mime": record.get("mime"),
        "size": record.get("size"),
        "status": record.get("status"),
        "error": record.get("error"),
        "created_at": record.get("created_at"),
        "preview": preview,
    }
