"""附件相关的 HTTP 接口（Blueprint）。

对外暴露三条 REST：
  POST   /attachments                 上传一个文件（multipart/form-data，字段名 file）
  GET    /attachments/<id>/raw        按 UUID 读原文件（图片预览/下载，刻意免登录）
  DELETE /attachments/<id>            删除附件（记录 + 磁盘文件）

鉴权策略：写操作（上传/删除）必须登录；读原文故意放开——<img> 标签带不了
Authorization 头，而附件 id 是不可枚举的 UUID，靠「知道 id 即可访问」保护。
"""

import os

from flask import Blueprint, jsonify, request, send_file

import db
from attachment.config import AttachmentError
from attachment.store import public_view, save_upload, storage_path
from lowcode_auth import require_user
from lowcode_store import LowcodeError

# 蓝图：由主应用在创建时 register_blueprint(attachment_bp, url_prefix=...)。
attachment_bp = Blueprint("attachment", __name__)


@attachment_bp.errorhandler(LowcodeError)
def _on_attachment_auth_error(exc: LowcodeError):
    """未登录/令牌过期统一 401 JSON。

    require_user() 在未登录时抛 LowcodeError（自带 HTTP code），
    这里统一转成 JSON 错误体，避免 Flask 默认的 HTML 错误页。
    """
    return jsonify({"error": str(exc)}), exc.code


@attachment_bp.route("/attachments", methods=["POST"])
def upload_attachment():
    """上传单个附件（multipart/form-data，字段名 file）。

    返回 201 + 附件元数据；解析失败也返回 201，但 status=failed 且带 error。
    校验类错误（类型不支持/超限/空文件）→ 400；未预期异常 → 500。
    """
    require_user()  # 附件属于聊天/低代码功能，需登录
    storage = request.files.get("file")
    if storage is None:
        return jsonify({"error": "缺少 file 字段（请用 multipart/form-data 上传）"}), 400
    try:
        record = save_upload(storage)
    except AttachmentError as exc:
        # 用户输入类校验失败：把原因原样回前端，便于提示。
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        # 兜底：数据库/磁盘等服务端异常，不泄露内部栈，只给一句话。
        return jsonify({"error": f"上传失败：{exc}"}), 500
    # 用 public_view 投影，不下发磁盘路径等敏感字段。
    return jsonify(public_view(record)), 201


@attachment_bp.route("/attachments/<string:attachment_id>/raw", methods=["GET"])
def get_attachment_raw(attachment_id):
    """读取附件原文件（图片预览、下载都用它）。

    刻意不要求登录：<img> 标签带不了自定义请求头，调试页也直接引用这个地址；
    id 是不可猜的 UUID，同理同源。上传/删除仍然要登录。
    """
    record = db.get_attachment(attachment_id)
    if record is None:
        return jsonify({"error": f"附件 {attachment_id} 不存在"}), 404
    path = storage_path(record)
    if not os.path.isfile(path):
        # 库里有记录但磁盘文件已丢（被外部误删/磁盘清理等），仍返回 404 而非 500。
        return jsonify({"error": "附件文件已丢失"}), 404
    # conditional=True 支持 Range 请求/断点续传与 304 缓存，浏览器 <img> 直接渲染。
    # as_attachment=False：浏览器内联预览而非强制下载；download_name 供另存为时用。
    return send_file(
        path,
        mimetype=record.get("mime") or None,
        download_name=record.get("name") or None,
        as_attachment=False,
        conditional=True,
    )


@attachment_bp.route("/attachments/<string:attachment_id>", methods=["DELETE"])
def remove_attachment(attachment_id):
    """删除附件（记录 + 磁盘文件）。"""
    require_user()  # 附件属于聊天/低代码功能，需登录
    record = db.get_attachment(attachment_id)
    if record is None:
        return jsonify({"error": f"附件 {attachment_id} 不存在"}), 404
    # 先删库记录：即使删文件失败，记录没了也不会再被引用。
    db.delete_attachment(attachment_id)
    try:
        os.remove(storage_path(record))
    except OSError:
        # 文件已被删/无权限等：忽略即可——库记录已先删，不会再被引用。
        # 注意：过期附件查询 db.list_expired_attachments 虽已预留，但当前并未
        # 接线任何定时任务，所以这里的磁盘残留暂时不会被自动清理，仅占存储空间。
        pass
    return jsonify({"message": f"附件 {attachment_id} 已删除"})
