"""聊天附件：上传、解析、入模。

整体思路（两步式）：
  1) 前端先 POST /attachments 上传文件 → 服务端落盘 + 解析 + 落库 → 返回附件 id
  2) 聊天时前端只传附件 id，服务端按「模型需要的参数形态」组装消息

拆包后各模块职责：
  config.py   配置（目录、上限、类型白名单、MIME）
  parsers.py  文本抽取（csv/xlsx/pdf/docx）+ 图片压缩
  store.py    上传落盘、查询视图
  blocks.py   组装入模 content
  routes.py   HTTP 接口

本文件只做再导出，保持 `from attachment import xxx` 这种老写法继续可用。
"""

from attachment.blocks import build_content_blocks
from attachment.config import (
    ACCEPT_ATTR,
    ALLOWED_EXTS,
    AttachmentError,
    IMAGE_KEEP_TURNS,
    IMAGE_MAX_EDGE,
    KIND_BY_EXT,
    KIND_LABELS,
    MAX_ATTACHMENT_TEXT,
    MAX_ATTACHMENT_TEXT_TOTAL,
    MAX_IMAGE_MB,
    MAX_SHEET_COLS,
    MAX_SHEET_ROWS,
    MAX_UPLOAD_MB,
    MODEL_VISION,
    UPLOAD_DIR,
    guess_mime,
    supported_summary,
)
from attachment.parsers import extract_text, shrink_image_if_needed
from attachment.routes import attachment_bp
from attachment.store import public_view, save_upload, storage_path

__all__ = [
    "ACCEPT_ATTR",
    "ALLOWED_EXTS",
    "AttachmentError",
    "IMAGE_KEEP_TURNS",
    "IMAGE_MAX_EDGE",
    "KIND_BY_EXT",
    "KIND_LABELS",
    "MAX_ATTACHMENT_TEXT",
    "MAX_ATTACHMENT_TEXT_TOTAL",
    "MAX_IMAGE_MB",
    "MAX_SHEET_COLS",
    "MAX_SHEET_ROWS",
    "MAX_UPLOAD_MB",
    "MODEL_VISION",
    "UPLOAD_DIR",
    "attachment_bp",
    "build_content_blocks",
    "extract_text",
    "guess_mime",
    "public_view",
    "save_upload",
    "shrink_image_if_needed",
    "storage_path",
    "supported_summary",
]
