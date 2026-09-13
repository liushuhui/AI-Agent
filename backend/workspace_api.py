"""工作目录相关的 HTTP 接口。

前端用它做三件事：
  1) 选目录：POST /workspace/pick —— 在服务端所在机器上弹系统选择框，
     拿到真实绝对路径后直接设为工作目录（推荐方式）；
  2) 备选：GET /fs/dirs + POST /workspace —— 后端没有图形界面时，
     退化成在网页里浏览服务端目录；
  3) 展示：GET /workspace、GET /workspace/file —— 文件清单与预览。
"""

from flask import Blueprint, jsonify, request

from AIagent.files import browse_dirs, read_text, workspace_tree
from AIagent.folder_dialog import FolderDialogError, pick_folder
from AIagent.workspace import WorkspaceError, get_root, set_root

workspace_bp = Blueprint("workspace", __name__)


def _fail(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


def _payload(extra: dict | None = None) -> dict:
    """统一的返回体：当前工作目录 + 文件清单（没选目录时也是同样形状）。"""
    root = get_root()
    data = (
        {"root": None, "entries": [], "truncated": False}
        if root is None
        else workspace_tree()
    )
    return {**(extra or {}), **data}


@workspace_bp.route("/fs/dirs", methods=["GET"])
def list_fs_dirs():
    """浏览服务端目录，给前端的「打开文件夹」选择器用。

    查询参数 path 留空时从用户主目录开始。
    """
    try:
        return jsonify(browse_dirs(request.args.get("path")))
    except WorkspaceError as exc:
        return _fail(exc)


@workspace_bp.route("/workspace", methods=["GET"])
def get_workspace():
    """当前工作目录 + 文件清单（没选过则 root 为 null）。"""
    return jsonify(_payload())


@workspace_bp.route("/workspace/pick", methods=["POST"])
def pick_workspace():
    """弹本机「选择文件夹」对话框，选中即设为工作目录。

    这是阻塞调用：对话框关掉之前不会返回，所以前端要把按钮置为等待态。
    用户取消时返回 {"cancelled": true} + 现有的目录状态（不变）。
    """
    root = get_root()
    try:
        path = pick_folder(initial_dir=str(root) if root else None)
    except FolderDialogError as exc:
        return _fail(exc, status=500)

    if not path:
        return jsonify(_payload({"cancelled": True}))
    try:
        set_root(path)
    except WorkspaceError as exc:
        return _fail(exc)
    return jsonify(_payload({"cancelled": False}))


@workspace_bp.route("/workspace", methods=["POST"])
def set_workspace():
    """直接指定工作目录。请求体 {"path": "D:\\\\code\\\\demo"}。"""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return _fail(WorkspaceError("path 为必填"))
    try:
        set_root(path)
    except WorkspaceError as exc:
        return _fail(exc)
    # 顺手把文件清单带回去，前端不用再发一次请求
    return jsonify(_payload())


@workspace_bp.route("/workspace/file", methods=["GET"])
def get_workspace_file():
    """预览工作区内的单个文本文件。查询参数 path 是相对工作目录的路径。"""
    relative = (request.args.get("path") or "").strip()
    if not relative:
        return _fail(WorkspaceError("path 为必填"))
    try:
        return jsonify(read_text(relative))
    except WorkspaceError as exc:
        return _fail(exc)
