"""附件相关的 HTTP 接口（Blueprint）。"""

import os

from flask import Blueprint, jsonify, request, send_file

import db
from attachment.config import AttachmentError
from attachment.store import public_view, save_upload, storage_path
from lowcode_auth import require_user
from lowcode_store import LowcodeError

attachment_bp = Blueprint("attachment", __name__)


@attachment_bp.errorhandler(LowcodeError)
def _on_attachment_auth_error(exc: LowcodeError):
    """未登录/令牌过期统一 401 JSON。"""
    return jsonify({"error": str(exc)}), exc.code


@attachment_bp.route("/attachments", methods=["POST"])
def upload_attachment():
    """上传单个附件（multipart/form-data，字段名 file）。

    返回 201 + 附件元数据；解析失败也返回 201，但 status=failed 且带 error。
    """
    require_user()  # 附件属于聊天/低代码功能，需登录
    storage = request.files.get("file")
    if storage is None:
        return jsonify({"error": "缺少 file 字段（请用 multipart/form-data 上传）"}), 400
    try:
        record = save_upload(storage)
    except AttachmentError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"上传失败：{exc}"}), 500
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
        return jsonify({"error": "附件文件已丢失"}), 404
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
    db.delete_attachment(attachment_id)
    try:
        os.remove(storage_path(record))
    except OSError:
        pass
    return jsonify({"message": f"附件 {attachment_id} 已删除"})
