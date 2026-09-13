"""配置：落盘目录、各类上限、支持的文件类型。

可用环境变量（全部可选，有默认值）：
  UPLOAD_DIR                附件落盘目录，默认 ./uploads
  MAX_UPLOAD_MB             单个文件大小上限，默认 20
  MAX_IMAGE_MB              图片原始大小上限，默认 8
  MAX_ATTACHMENT_TEXT       单个附件注入模型的文本上限（字符），默认 8000
  MAX_ATTACHMENT_TEXT_TOTAL 单条消息里所有附件注入文本的总上限，默认 30000
  MAX_SHEET_ROWS            表格类附件最多解析的行数，默认 200
  MAX_SHEET_COLS            表格类附件最多解析的列数，默认 30
  MODEL_VISION              模型是否支持读图（true/false），默认 true
  IMAGE_KEEP_TURNS          历史里保留图片块的最近轮数，默认 2
  IMAGE_MAX_EDGE            图片压缩后的最长边像素，默认 1600
"""

import os

from dotenv import load_dotenv

# 本模块在 import 期就要读环境变量（下面的常量），所以必须在这里先加载 .env。
# 注意：load_dotenv 只影响「它之后」执行的 os.getenv，加载晚一步就读不到 .env 的值。
load_dotenv(override=True)

# ---------------- 配置 ----------------
# 注意：本文件在 attachment/ 子目录里，项目根要往上剥两层，
# 否则 UPLOAD_DIR 会变成 attachment/uploads。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.getenv("UPLOAD_DIR") or os.path.join(BASE_DIR, "uploads")

MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_IMAGE_MB = float(os.getenv("MAX_IMAGE_MB", "8"))
MAX_ATTACHMENT_TEXT = int(os.getenv("MAX_ATTACHMENT_TEXT", "8000"))
MAX_ATTACHMENT_TEXT_TOTAL = int(os.getenv("MAX_ATTACHMENT_TEXT_TOTAL", "30000"))
MAX_SHEET_ROWS = int(os.getenv("MAX_SHEET_ROWS", "200"))
MAX_SHEET_COLS = int(os.getenv("MAX_SHEET_COLS", "30"))
IMAGE_MAX_EDGE = int(os.getenv("IMAGE_MAX_EDGE", "1600"))


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


MODEL_VISION = _env_flag("MODEL_VISION", True)
IMAGE_KEEP_TURNS = int(os.getenv("IMAGE_KEEP_TURNS", "2"))

MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)
MAX_IMAGE_BYTES = int(MAX_IMAGE_MB * 1024 * 1024)

# ---------------- 类型白名单 ----------------
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "gif"}
WORD_EXTS = {"docx"}
SHEET_EXTS = {"xlsx", "csv"}
PDF_EXTS = {"pdf"}
TEXT_EXTS = {
    "txt", "md", "markdown", "json", "log", "yaml", "yml", "xml", "html", "htm",
    "py", "js", "jsx", "ts", "tsx", "css", "sql", "ini", "conf", "toml", "sh",
}

KIND_BY_EXT = {}
KIND_BY_EXT.update({e: "image" for e in IMAGE_EXTS})
KIND_BY_EXT.update({e: "pdf" for e in PDF_EXTS})
KIND_BY_EXT.update({e: "word" for e in WORD_EXTS})
KIND_BY_EXT.update({e: "sheet" for e in SHEET_EXTS})
KIND_BY_EXT.update({e: "text" for e in TEXT_EXTS})

KIND_LABELS = {
    "image": "图片",
    "pdf": "PDF",
    "word": "Word 文档",
    "sheet": "表格",
    "text": "文本",
    "other": "文件",
}

ALLOWED_EXTS = set(KIND_BY_EXT)

MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "json": "application/json",
    "xml": "application/xml",
    "html": "text/html",
    "htm": "text/html",
    "yaml": "text/yaml",
    "yml": "text/yaml",
}


def guess_mime(ext: str, provided: str | None) -> str:
    """上传方给的 MIME 不可信：缺失或过于笼统（octet-stream）时按扩展名推断。

    这关系到 /attachments/<id>/raw 能否被 <img> 直接渲染。
    """
    provided = (provided or "").strip().lower()
    if provided and provided != "application/octet-stream":
        return provided
    return MIME_BY_EXT.get(ext, "application/octet-stream")


# 前端 <input accept> 用的字符串，与 ALLOWED_EXTS 保持一致
ACCEPT_ATTR = ",".join(sorted(f".{e}" for e in ALLOWED_EXTS))


class AttachmentError(Exception):
    """附件校验/解析失败：属于用户输入问题，接口返回 400。"""


def supported_summary() -> dict:
    """支持的类型说明，用于接口文档与错误提示。"""
    return {
        "图片": sorted(IMAGE_EXTS),
        "PDF": sorted(PDF_EXTS),
        "Word": sorted(WORD_EXTS),
        "表格": sorted(SHEET_EXTS),
        "文本": sorted(TEXT_EXTS),
        "单文件上限": f"{MAX_UPLOAD_MB:g}MB",
        "模型读图": MODEL_VISION,
    }
