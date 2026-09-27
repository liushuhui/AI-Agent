"""上传落盘与对外视图。

本模块是「上传链路」的核心编排：接收一个 Werkzeug 上传文件（Flask 的
request.files 项），依次做 安全校验 → 落盘 → 图片压缩 → 文本抽取 → 写 MySQL，
最后把一条附件记录交给上游。磁盘文件名与对外 id 解耦——
磁盘上是 uuid，对外/对模型只暴露数据库主键 id，绝不暴露绝对路径。
"""

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
    """由记录推出磁盘绝对路径（stored_name 是 uuid + 白名单后缀，天然防路径穿越）。

    注意：只在服务端内部使用（读写文件、删除文件），绝不能透传给前端——
    对外路径一律走 public_view 或 /attachments/<id>/raw 这个不可猜的 UUID 路由。
    """
    return os.path.join(UPLOAD_DIR, record["stored_name"])


def ensure_upload_dir() -> None:
    """确保上传目录存在；exist_ok=True 保证并发/重复调用也不报错。"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_upload(storage) -> dict:
    """校验 + 落盘 + 解析 + 落库，返回附件记录。校验失败抛 AttachmentError。

    入参 storage 是 Flask request.files 里的文件对象（Werkzeug FileStorage），
    用到它的 .filename / .mimetype / .read() 三个接口。

    副作用：在 UPLOAD_DIR 写文件（图片可能被改写/另存为 .jpg），并向 MySQL
    attachments 表插入一行；任一步抛 AttachmentError 都表示用户输入问题，
    此时不落盘、不入库。
    """
    raw_name = (storage.filename or "").strip()
    if not raw_name:
        raise AttachmentError("文件名为空")

    # ---- 1) 路径穿越防护 ----
    # 只取最后一段文件名，防止 ../ 之类的路径穿越；同时兼容 Windows 反斜杠。
    name = os.path.basename(raw_name.replace("\\", "/")) or "未命名文件"
    # 无扩展名时 ext 为空串，下面白名单判定自然失败，拒绝上传。
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTS:
        raise AttachmentError(
            f"不支持的文件类型：.{ext or '未知'}（支持：{'、'.join(sorted(ALLOWED_EXTS))}）"
        )

    kind = KIND_BY_EXT[ext]
    # 一次性把整个文件读进内存做大小校验；附件有上限（图片 8MB / 其余 20MB），
    # 内存占用可控，同时也免去对临时文件的二次读取。
    data = storage.read()
    size = len(data)
    if size == 0:
        raise AttachmentError("文件内容为空")

    # ---- 2) 大小上限 ----
    # 图片单独用更严的上限（MAX_IMAGE_BYTES），其余文件走通用上限。
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_UPLOAD_BYTES
    if size > limit:
        raise AttachmentError(
            f"文件超过上限（{KIND_LABELS[kind]}最大 {limit / 1024 / 1024:g}MB）"
        )

    # ---- 3) 落盘 ----
    ensure_upload_dir()
    # 文件名用 uuid4，避免重名覆盖、也避免用户原始文件名里的特殊字符引发问题；
    # 保留白名单内的原始扩展名，供后续按扩展名识别类型。
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, stored_name)
    with open(path, "wb") as handle:
        handle.write(data)

    mime = guess_mime(ext, storage.mimetype)

    # ---- 4) 图片压缩（仅图片）----
    # 压缩可能把图片另存为 .jpg（改路径、改扩展名、改大小），所以三者都要回写，
    # 并据此刷新 stored_name 与 MIME。非图片原样返回，无副作用。
    if kind == "image":
        path, ext, size = shrink_image_if_needed(path, ext, size)
        stored_name = os.path.basename(path)
        mime = MIME_BY_EXT.get(ext, mime)

    # ---- 5) 文本抽取（失败仅标记，不阻断上传）----
    # 解析失败不阻断上传：记录 status=failed + 原因，前端自行决定是否发送。
    # 这样用户上传一个损坏/扫描件 PDF 仍能拿到附件 id、能预览原文件，
    # 只是入模时 body 会显示「解析失败」。
    status, error, text_content = "ready", None, ""
    try:
        text_content = extract_text(kind, path, data)
    except AttachmentError as exc:
        # 已知的、对用户友好的解析失败（如扫描件 PDF、缺依赖），原样透传原因。
        status, error = "failed", str(exc)
    except Exception as exc:  # 解析库抛出的意外错误
        # 兜底：任何未预期异常都不冒泡成 500，统一降级为 failed 记录。
        status, error = "failed", f"解析失败：{exc}"

    # ---- 6) 写 MySQL ----
    # 把解析正文一并入库，这样后续组装消息时无需再次解析磁盘文件。
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
    """对外的附件视图：不含磁盘路径，文本只给一小段预览。

    这是面向前端/接口的「投影」：
      - 故意不下发 stored_name / 绝对路径，避免泄露服务器目录结构；
      - text_content 可能长达上万字，这里只截 preview_chars 做列表预览，
        完整正文留到真正入模时由 blocks.py 按预算取用。
    """
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
