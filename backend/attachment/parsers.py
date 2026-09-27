"""文本抽取：把各种格式的附件解析成正文，以及上传前的图片压缩。

设计要点：
  - 重依赖（openpyxl / pypdf / python-docx / Pillow）一律「函数内延迟 import」，
    这样未装某个库时不影响整个包导入；缺哪个就在对应解析时报出可执行的安装提示。
  - 所有解析器统一输出「纯文本 / Markdown」字符串，方便直接拼进模型上下文。
  - 文本类附件要兼容中文常见编码，不能默认按 UTF-8 解。
"""

import csv
import io
import os

from attachment.config import (
    AttachmentError,
    IMAGE_MAX_EDGE,
    MAX_IMAGE_BYTES,
    MAX_SHEET_COLS,
    MAX_SHEET_ROWS,
)


# ---------------- 文本解码 / 表格转 Markdown ----------------
def _decode(data: bytes) -> str:
    """按常见中文编码依次尝试解码，最后兜底替换非法字节。

    顺序有讲究：
      - utf-8-sig 先于 utf-8：去掉 Windows 导出文件常见的 BOM 头；
      - 再 utf-8：现代编码；
      - gb18030：覆盖 GBK/GB2312（大陆老文件最常见）；
      - big5：繁体中文。
    全部失败时用 errors="replace" 把坏字节替换成 �，保证绝不因编码抛错。
    """
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _rows_to_markdown(rows: list) -> str:
    """把二维表转成 Markdown 表格，首行当表头。

    注意：openpyxl 按固定列宽取数会产生大量尾部空单元格，这里统一裁掉，
    否则表格会多出一堆空列，白白吃掉上下文。
    """
    def trim(row: list) -> list:
        # 末尾连续空单元格裁掉，避免 openpyxl 固定列宽取数带来的一堆空列。
        cells = [("" if c is None else str(c)) for c in row]
        end = len(cells)
        while end > 0 and not cells[end - 1].strip():
            end -= 1
        return cells[:end]

    rows = [trim(r) for r in rows]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    # 所有行补齐到最大列宽，否则 Markdown 表格竖线对不齐、渲染错乱。
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    def esc(value: str) -> str:
        # 单元格里的 | 会破坏表格语法需转义；换行/回车压成空格，保持一行一记录。
        return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()

    lines = [
        "| " + " | ".join(esc(c) for c in rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:])
    return "\n".join(lines)


def _parse_csv(data: bytes) -> str:
    # CSV 不走磁盘路径，直接吃内存里的字节（store 已把整文件读进 data）。
    text = _decode(data)
    rows = list(csv.reader(io.StringIO(text)))
    truncated = len(rows) > MAX_SHEET_ROWS
    rows = rows[:MAX_SHEET_ROWS]
    # 列数也同步截断，防止宽表把上下文撑爆。
    table = _rows_to_markdown([r[:MAX_SHEET_COLS] for r in rows])
    if truncated:
        table += f"\n（表格行数过多，仅解析前 {MAX_SHEET_ROWS} 行）"
    return table


def _parse_xlsx(path: str) -> str:
    # 延迟 import：未装 openpyxl 时给出 pip 安装提示，而不是 ImportError 裸奔。
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # 依赖缺失时给出可执行的提示
        raise AttachmentError("服务端未安装 openpyxl，无法解析 Excel（pip install openpyxl）") from exc

    # read_only 流式读、data_only 取公式计算后的缓存值（而不是公式串），省内存。
    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            # 多读一行用于判断「是否被 MAX_SHEET_ROWS 截断」。
            rows = [
                list(row)
                for row in ws.iter_rows(
                    max_row=MAX_SHEET_ROWS + 1, max_col=MAX_SHEET_COLS, values_only=True
                )
            ]
            table = _rows_to_markdown(rows[:MAX_SHEET_ROWS])
            if not table:
                continue
            head = f"### 工作表：{ws.title}"
            if len(rows) > MAX_SHEET_ROWS:
                head += f"（仅前 {MAX_SHEET_ROWS} 行 × {MAX_SHEET_COLS} 列）"
            parts.append(f"{head}\n{table}")
    finally:
        # read_only 模式必须显式关，否则文件句柄/临时文件泄漏。
        wb.close()
    return "\n\n".join(parts)


def _parse_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise AttachmentError("服务端未安装 pypdf，无法解析 PDF（pip install pypdf）") from exc

    reader = PdfReader(path)
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # 单页损坏不影响整篇
            text = ""
        if text.strip():
            pages.append(f"### 第 {index} 页\n{text.strip()}")
    # 一页都没抽出文本，基本是扫描件/纯图片 PDF——pypdf 无 OCR 能力，直接提示用户。
    if not pages:
        raise AttachmentError("该 PDF 未解析出文本（可能是扫描件/纯图片），请改用图片或提供文字版")
    return "\n\n".join(pages)


def _parse_docx(path: str) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise AttachmentError("服务端未安装 python-docx，无法解析 Word（pip install python-docx）") from exc

    document = Document(path)
    # 普通段落按顺序抽取，跳过空段。
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    # 内嵌表格单独转 Markdown 表格，并标序号，避免和正文混在一起。
    for index, table in enumerate(document.tables, start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        markdown = _rows_to_markdown(rows)
        if markdown:
            parts.append(f"### 表格 {index}\n{markdown}")
    return "\n".join(parts)


def extract_text(kind: str, path: str, data: bytes) -> str:
    """按类型抽取正文；图片不需要解析，直接返回空串。

    分发依据是 config 里的 kind（见 store.save_upload 阶段确定）。
    CSV 走内存字节、xlsx/pdf/docx 走磁盘路径——前者轻、后者库需要文件句柄。
    """
    if kind == "image":
        return ""
    if kind == "pdf":
        return _parse_pdf(path)
    if kind == "word":
        return _parse_docx(path)
    if kind == "sheet":
        if path.lower().endswith(".csv"):
            return _parse_csv(data)
        return _parse_xlsx(path)
    if kind == "text":
        return _decode(data)
    return ""


# ---------------- 图片压缩（可选依赖 Pillow） ----------------
def shrink_image_if_needed(path: str, ext: str, size: int) -> tuple:
    """把超大/像素过多的图片压到 IMAGE_MAX_EDGE 以内，返回 (path, ext, size)。

    没装 Pillow、或（既没超像素也没超体积）时原样返回，不影响主流程。
    之所以要压：一是模型多模态读图对分辨率有上限/计费按 token，二是 base64
    内嵌进请求体时体积越大越容易超时。压缩后统一转 JPEG 以获得更小体积。
    """
    if ext == "gif":
        # 动图压成静态 JPEG 会丢帧/丢动画，直接放行。
        return path, ext, size
    try:
        from PIL import Image
    except ImportError:
        # 没装 Pillow 就不压，原图照样上传，只是没享受压缩收益。
        return path, ext, size

    try:
        with Image.open(path) as img:
            width, height = img.size
            longest = max(width, height)
            need_resize = longest > IMAGE_MAX_EDGE
            # 既没超像素、体积又在安全线内（上限一半），直接原图返回，不做无谓重编码。
            if not need_resize and size <= MAX_IMAGE_BYTES // 2:
                return path, ext, size
            if need_resize:
                # 按最长边等比缩放，另一边同步缩小；max(1,...) 防极端小图除零。
                scale = IMAGE_MAX_EDGE / float(longest)
                img = img.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale)))
                )
            # 统一转 RGB 存 JPEG：RGBA/调色板等格式 JPEG 不支持，RGB 是最稳的公共格式。
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
    except Exception:  # 压缩失败就用原图，不能因此让上传失败
        return path, ext, size

    new_data = buffer.getvalue()
    # 只是体积偏大时，压缩没变小就保留原图；像素超限时则以"缩到上限"为准
    if len(new_data) >= size and not need_resize:
        return path, ext, size

    # 落盘为 .jpg（扩展名变了，调用方据此刷新 stored_name / MIME / size）。
    new_path = os.path.splitext(path)[0] + ".jpg"
    with open(new_path, "wb") as handle:
        handle.write(new_data)
    # 原格式文件删除失败也无妨：新 .jpg 已落盘、调用方会刷新 stored_name，
    # 旧文件就此成为不再被引用的残留（当前没有定时清理任务，仅占磁盘），不影响主流程。
    if new_path != path:
        try:
            os.remove(path)
        except OSError:
            pass
    return new_path, "jpg", len(new_data)
