"""会话（对话历史）相关的 HTTP 接口。

设计取舍
--------
会话数据存在服务端（MySQL），而不是由前端每次把全量历史传上来：
  1. 历史不可篡改 —— 前端传来的 assistant/工具消息可以被伪造，等于绕过护栏；
  2. 传输量恒定 —— 每轮只传本轮新消息，不再 O(n²) 地重复上传旧历史；
  3. 可持久化 —— 刷新页面、换设备、审计都靠这一份数据。

接口一览
--------
  GET    /conversations            会话列表（最近更新的排最前）
  POST   /conversations            新建会话（可传 title）
  GET    /conversations/<id>       会话详情 + 全部消息
  PUT    /conversations/<id>       重命名
  DELETE /conversations/<id>       删除会话及其消息

发消息走 SSE，在 message.py 里（/conversations/<id>/messages）。
"""

from flask import Blueprint, jsonify, request

import db
from lowcode_auth import can_access, require_user
from lowcode_store import LowcodeError
from message import release_conversation_threads

conversation_bp = Blueprint("conversation", __name__)


@conversation_bp.errorhandler(LowcodeError)
def _on_auth_error(exc: LowcodeError):
    """未登录/令牌过期统一 401 JSON（与低代码平台共用同一套登录）。"""
    return jsonify({"error": str(exc)}), exc.code

# 标题长度上限，与 conversations.title 的列宽（120）保持余量
TITLE_MAX = 60


def _fail(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _owned_or_404(conversation_id: str):
    """取当前用户有权访问的会话；不存在与无权限都按 404 回（不泄露存在性）。"""
    conversation = db.get_conversation(conversation_id)
    if conversation is None or not can_access(require_user(), conversation.get("owner_id", "")):
        return None, _fail(f"会话 {conversation_id} 不存在", 404)
    return conversation, None


@conversation_bp.route("/conversations", methods=["GET"])
def list_conversations():
    """会话列表（只返回当前用户自己的；管理员看全部）。"""
    user = require_user()
    owner = None if user["role"] == "admin" else user["id"]
    return jsonify({"conversations": db.list_conversations(owner_id=owner)})


@conversation_bp.route("/conversations", methods=["POST"])
def create_conversation():
    """新建会话。请求体可选 {"title": "..."}，不传则用默认标题。

    这里「允许存在空会话」：用户点一次「新建对话」就会有一行，
    发第一条消息时再用内容自动改标题（见 message.py）。
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:TITLE_MAX] or db.DEFAULT_TITLE
    user = require_user()
    return jsonify(db.create_conversation(title, owner_id=user["id"])), 201


@conversation_bp.route("/conversations/<string:conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    """会话详情 + 全部消息（按时间正序），用于点开历史对话时回填界面。"""
    conversation, error = _owned_or_404(conversation_id)
    if error:
        return error
    return jsonify(
        {"conversation": conversation, "messages": db.list_messages(conversation_id)}
    )


@conversation_bp.route("/conversations/<string:conversation_id>", methods=["PUT"])
def rename_conversation(conversation_id):
    """重命名。请求体 {"title": "..."}。"""
    _, error = _owned_or_404(conversation_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return _fail("title 为必填")
    conversation = db.rename_conversation(conversation_id, title[:TITLE_MAX])
    if conversation is None:
        return _fail(f"会话 {conversation_id} 不存在", 404)
    return jsonify(conversation)


@conversation_bp.route("/conversations/<string:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    """删除会话及其全部消息。

    注意：附件文件不在这里删（附件表与会话表没有从属关系），
    否则「同一个附件被多个会话引用」时会误删。
    """
    # 先确认会话归当前用户，再清检查点；不存在/无权限一律 404
    _, error = _owned_or_404(conversation_id)
    if error:
        return error
    # 先清检查点再删记录：线程 id 记在消息表里，删了消息就找不到了。
    # 挂起中的审批与待办清单只存在检查点里，对话都不要了，一并清掉。
    release_conversation_threads(conversation_id)
    if not db.delete_conversation(conversation_id):
        return _fail(f"会话 {conversation_id} 不存在", 404)
    return jsonify({"message": f"会话 {conversation_id} 已删除"})
