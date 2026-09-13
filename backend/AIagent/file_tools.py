"""给模型的「代码读写」工具：在工作目录内列目录、读文件、改代码、增删文件。

约定：
  - 所有 path 都是**工作目录内的相对路径**（如 `src/app.py`），根目录就是 "."。
  - 出错不抛异常，而是返回以「失败：」开头的字符串，让模型看到原因后自己调整。

改文件的三个工具（write_file / replace_in_file / delete_path）在
approval.py 里被列为需要人工审批，确认后才会真正落盘。
"""

from langchain_core.tools import tool

from AIagent.files import (
    delete_path as _delete_path,
    list_entries,
    read_text,
    replace_in_text,
    search_in_files,
    write_text,
)
from AIagent.workspace import WorkspaceError


def _run(action, *args, **kwargs) -> str:
    """统一把结果/异常转成工具返回值。"""
    try:
        return action(*args, **kwargs)
    except WorkspaceError as exc:
        return f"失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 任何意外都要变成模型能读懂的话
        return f"失败：{type(exc).__name__}: {exc}"


@tool
def list_files(path: str = "") -> str:
    """列出工作目录（或其中某个子目录）下的文件与子目录。

    Args:
        path: 工作目录内的相对路径，留空表示工作目录根。例如 "" 或 "src"。

    Returns:
        目录与文件清单；path 指向文件时会直接返回该文件内容。
    """
    result = _run(list_entries, path)
    if isinstance(result, str):
        return result
    lines = [f"目录：{result['path']}"]
    if result.get("is_dir") is False:
        content = result["content"]
        return f"（{result['path']} 是文件）\n{content['content']}"
    if not result["dirs"] and not result["files"]:
        return f"目录：{result['path']}\n（空目录）"
    if result["dirs"]:
        lines.append("子目录：\n  " + "\n  ".join(result["dirs"]))
    if result["files"]:
        lines.append("文件：\n  " + "\n  ".join(result["files"]))
    return "\n".join(lines)


@tool
def read_file(path: str) -> str:
    """读取工作目录内某个文本文件的完整内容。

    修改文件前务必先读一遍，拿到真实原文再改。

    Args:
        path: 工作目录内的相对路径，例如 "app.py" 或 "src/main.tsx"。

    Returns:
        文件内容；超过上限时会被截断并注明。
    """
    result = _run(read_text, path)
    if isinstance(result, str):
        return result
    note = "\n……（文件过大，只返回了前一部分）" if result["truncated"] else ""
    return f"===== {result['path']}（{result['size']} 字节）=====\n{result['content']}{note}"


@tool
def search_code(keyword: str, path: str = "") -> str:
    """在工作目录内按关键字搜索代码（纯文本匹配，返回命中行）。

    Args:
        keyword: 要查找的字符串，例如 "def create_agent"。
        path: 限定搜索范围的相对路径，留空表示整个工作目录。

    Returns:
        形如 "文件:行号: 内容" 的命中列表。
    """
    hits = _run(search_in_files, keyword, path)
    if isinstance(hits, str):
        return hits
    if not hits:
        return f"没有找到包含 {keyword!r} 的位置。"
    return "\n".join(hits)


@tool
def write_file(path: str, content: str) -> str:
    """新建文件，或整体覆盖已有文件（父目录会自动创建）。

    只想改几行时请优先用 replace_in_file，整体覆盖会丢掉未写进 content 的内容。

    Args:
        path: 工作目录内的相对路径，例如 "src/utils.py"。
        content: 要写入的完整文件内容。

    Returns:
        写入结果说明。
    """
    result = _run(write_text, path, content)
    if isinstance(result, str):
        return result
    return f"{result['action']}成功：{result['path']}（{result['size']} 字节）"


@tool
def replace_in_file(path: str, old_text: str, new_text: str) -> str:
    """把文件里的一段原文精确替换成新内容（最常用的改代码方式）。

    old_text 必须与文件中的原文完全一致（含缩进），且在文件中**只出现一次**；
    否则会失败，需要多带几行上下文再试。

    Args:
        path: 工作目录内的相对路径。
        old_text: 要被替换掉的原文片段。
        new_text: 替换成的新片段；传空字符串表示删除这段原文。

    Returns:
        替换结果说明。
    """
    result = _run(replace_in_text, path, old_text, new_text)
    if isinstance(result, str):
        return result
    return f"{result['action']}成功：{result['path']}（现在 {result['size']} 字节）"


@tool
def delete_path(path: str) -> str:
    """删除工作目录内的文件（目录只允许删空的）。

    Args:
        path: 工作目录内的相对路径。

    Returns:
        删除结果说明。
    """
    result = _run(_delete_path, path)
    if isinstance(result, str):
        return result
    return f"{result['action']}成功：{result['path']}"


# 只读工具：不需要人工审批
READ_ONLY_TOOLS = [list_files, read_file, search_code]
# 会改动磁盘的工具：approval.py 里都要求人工审批
WRITE_TOOLS = [write_file, replace_in_file, delete_path]
FILE_TOOLS = [*READ_ONLY_TOOLS, *WRITE_TOOLS]
