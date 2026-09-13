"""当前工作目录：用户在前端选定的、允许 Agent 读写代码的文件夹。

路径安全都收在 resolve_path()：只接受工作目录内的相对路径，
绝对路径、`..` 越界、指向目录外的软链接都会被拒绝。

注意：这里是「进程内单例」。本项目是本地单用户工具，够用；
若将来要多用户并发，得改成按会话存（放进 checkpointer 或 Redis）。
"""

import json
from pathlib import Path

from rich import print as rprint


class WorkspaceError(Exception):
    """工作目录相关的、可以直接展示给用户的错误。"""


# 选中的目录会落盘记下来：Flask 的 debug reloader 改一次代码就重启一次，
# 不记的话每次都要重新选一遍。
_STATE_FILE = Path(__file__).resolve().parent.parent / ".workspace.json"

_root: Path | None = None


def _restore() -> None:
    """启动时（import 时）把上次选的工作目录读回来；已失效就忽略。"""
    global _root
    if not _STATE_FILE.exists():
        return
    try:
        saved = Path(json.loads(_STATE_FILE.read_text(encoding="utf-8"))["root"])
    except (OSError, ValueError, KeyError):
        return
    if saved.is_dir():
        _root = saved


def get_root() -> Path | None:
    """当前工作目录；没设置过则返回 None。"""
    return _root


def set_root(path: str) -> Path:
    """设置工作目录，必须是已存在的文件夹。"""
    target = Path(path).expanduser()
    if not target.exists():
        raise WorkspaceError(f"路径不存在：{path}")
    if not target.is_dir():
        raise WorkspaceError(f"不是文件夹：{path}")

    global _root
    _root = target.resolve()
    try:
        _STATE_FILE.write_text(
            json.dumps({"root": str(_root)}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        # 记不下来不影响本次使用，但必须留痕：否则「重启后目录没了」很难查
        rprint(f"[yellow]工作目录没能记住（{exc}），重启后端后会丢失[/yellow]")
    return _root


def resolve_path(relative: str = "", *, must_exist: bool = False) -> Path:
    """把「工作目录内的相对路径」解析成绝对路径；越界抛 WorkspaceError。

      - "" 或 "." → 工作目录本身
      - 拒绝绝对路径（`/x`、`C:/x`、`\\\\server\\share`）
      - 解析后必须仍在工作目录内（`..` 与指向外部的软链接都会被挡住）
    """
    if _root is None:
        raise WorkspaceError("还没有选择工作目录，请先在前端点「打开文件夹」")

    raw = (relative or "").strip().replace("\\", "/")
    if raw in ("", "."):
        return _root

    # 绝对路径直接拒绝，避免绕过根目录
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise WorkspaceError(f"只接受工作目录内的相对路径，收到绝对路径：{relative}")

    # resolve() 之后比对，能一并挡住 `..` 和指向目录外的软链接
    target = (_root / raw).resolve()
    if target != _root and _root not in target.parents:
        raise WorkspaceError(f"路径超出工作目录范围：{relative}")

    if must_exist and not target.exists():
        raise WorkspaceError(f"文件或文件夹不存在：{relative}")
    return target


def relative_of(path: Path) -> str:
    """把绝对路径转回工作目录内的相对路径（用于返回给前端/模型）。"""
    if _root is None:
        return str(path)
    try:
        return path.relative_to(_root).as_posix() or "."
    except ValueError:
        return str(path)


_restore()
