"""工作目录内的文件操作。

同一套函数被两边共用：
  - Agent 的工具（AIagent/file_tools.py）
  - 前端的工作区接口（workspace_api.py）

所有出错都抛 WorkspaceError，message 已经是能直接展示的中文。
"""

import os
from pathlib import Path

from AIagent.workspace import (
    WorkspaceError,
    get_root,
    relative_of,
    resolve_path,
)

# 单次读取/预览的字节上限，避免把大文件整个塞进上下文或响应
MAX_READ_BYTES = 200_000
# 文件清单最多返回多少条
MAX_TREE_ENTRIES = 500
# 这些目录不进清单：依赖/缓存/版本库，列出来只会淹没有用信息
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}


def require_root() -> Path:
    root = get_root()
    if root is None:
        raise WorkspaceError("还没有选择工作目录，请先在前端点「打开文件夹」")
    return root


# ---------------- 浏览（供前端文件夹选择器） ----------------
def browse_dirs(path: str | None = None) -> dict:
    """列出某个绝对路径下的子文件夹（以及文件，仅名字和大小）。

    这是「选择工作目录」用的，所以不限制在工作目录内——用户正是要靠它去挑目录。
    文件也一并返回，是为了让「一个只有文件的目录」不至于看起来像空的
    （只给名字和大小，不返回内容）。
    """
    base = Path(path).expanduser() if path else Path.home()
    if not base.exists():
        raise WorkspaceError(f"文件夹不存在：{base}")
    if not base.is_dir():
        raise WorkspaceError(f"不是文件夹：{base}")
    base = base.resolve()

    try:
        children = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise WorkspaceError(f"没有权限读取：{base}") from exc

    dirs, files = [], []
    for item in children:
        if item.name.startswith("."):
            continue
        if item.is_dir():
            dirs.append({"name": item.name, "path": str(item)})
        elif item.is_file():
            try:
                size = item.stat().st_size
            except OSError:
                size = 0
            files.append({"name": item.name, "size": size})

    parent = base.parent
    return {
        "path": str(base),
        "parent": None if parent == base else str(parent),
        "dirs": dirs[:200],
        "files": files[:200],
    }


# ---------------- 清单 ----------------
def workspace_tree(max_entries: int = MAX_TREE_ENTRIES) -> dict:
    """工作区内的文件清单（相对路径），跳过依赖与缓存目录。"""
    root = require_root()
    entries: list[dict] = []
    truncated = False

    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if len(entries) >= max_entries:
                truncated = True
                break
            full = Path(current) / name
            try:
                size = full.stat().st_size
            except OSError:
                size = 0
            entries.append(
                {"path": relative_of(full), "name": name, "size": size}
            )
        if truncated:
            break

    return {"root": str(root), "entries": entries, "truncated": truncated}


def list_entries(relative: str = "") -> dict:
    """列出某个目录下的直接子项（模型调 list_files 时用）。"""
    target = resolve_path(relative, must_exist=True)
    if not target.is_dir():
        return {
            "path": relative,
            "is_dir": False,
            "content": read_text(relative),
        }

    dirs, files = [], []
    for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if item.name.startswith(".") or item.name in SKIP_DIRS:
            continue
        if item.is_dir():
            dirs.append(f"{relative_of(item)}/")
        else:
            try:
                files.append(f"{relative_of(item)}  ({item.stat().st_size} 字节)")
            except OSError:
                files.append(relative_of(item))
    return {"path": relative or ".", "dirs": dirs, "files": files}


# ---------------- 读 ----------------
def read_text(relative: str) -> dict:
    """读文本文件（有字节上限）。"""
    path = resolve_path(relative, must_exist=True)
    if path.is_dir():
        raise WorkspaceError(f"这是一个文件夹，不是文件：{relative}")
    size = path.stat().st_size
    content = path.read_bytes()[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    return {
        "path": relative_of(path),
        "size": size,
        "content": content,
        "truncated": size > MAX_READ_BYTES,
    }


# ---------------- 写 ----------------
def write_text(relative: str, content: str, *, append: bool = False) -> dict:
    """新建或覆盖文本文件；父目录不存在会自动建。"""
    path = resolve_path(relative)
    if path.exists() and path.is_dir():
        raise WorkspaceError(f"目标是一个文件夹：{relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8", newline="") as handle:
        handle.write(content)
    return {
        "path": relative_of(path),
        "action": "追加" if append else ("覆盖" if existed else "新建"),
        "size": path.stat().st_size,
    }


def replace_in_text(relative: str, old_text: str, new_text: str) -> dict:
    """把文件里的 old_text 整段替换成 new_text。

    要求 old_text 在文件里**只出现一次**：0 次说明写的片段不对，
    多次说明上下文不够精确，两种都直接报错让模型重新给片段。
    """
    if not old_text:
        raise WorkspaceError("old_text 不能为空")

    path = resolve_path(relative, must_exist=True)
    if path.is_dir():
        raise WorkspaceError(f"这是一个文件夹，不是文件：{relative}")

    content = path.read_text(encoding="utf-8", errors="replace")
    hits = content.count(old_text)
    if hits == 0:
        raise WorkspaceError(
            f"在 {relative} 里找不到要替换的内容，请先 read_file 核对原文后再试"
        )
    if hits > 1:
        raise WorkspaceError(
            f"要替换的内容在 {relative} 里出现了 {hits} 次，请多带几行上下文让它唯一"
        )

    updated = content.replace(old_text, new_text)
    path.write_text(updated, encoding="utf-8", newline="")
    return {"path": relative_of(path), "action": "替换", "size": len(updated.encode("utf-8"))}


def delete_path(relative: str) -> dict:
    """删除文件；目录只允许删空的（避免一条指令删掉一大片）。"""
    path = resolve_path(relative, must_exist=True)
    if path == get_root():
        raise WorkspaceError("不能删除工作目录本身")

    if path.is_dir():
        if any(path.iterdir()):
            raise WorkspaceError(f"文件夹非空，请先清空或逐个删除：{relative}")
        path.rmdir()
        return {"path": relative_of(path), "action": "删除空文件夹"}

    path.unlink()
    return {"path": relative_of(path), "action": "删除文件"}


# ---------------- 搜索 ----------------
def search_in_files(keyword: str, relative: str = "", limit: int = 40) -> list[str]:
    """在工作区内按关键字做纯文本搜索，返回「相对路径:行号: 内容」。"""
    if not keyword:
        raise WorkspaceError("keyword 不能为空")

    start = resolve_path(relative, must_exist=True)
    targets = [start] if start.is_file() else sorted(start.rglob("*"))

    hits: list[str] = []
    for path in targets:
        if len(hits) >= limit:
            break
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.stat().st_size > MAX_READ_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if keyword in line:
                hits.append(f"{relative_of(path)}:{number}: {line.strip()[:200]}")
                if len(hits) >= limit:
                    break
    return hits
