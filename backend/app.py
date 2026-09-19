# -*- coding: utf-8 -*-
"""
app.py —— REST API 服务入口（纯后端，不含前端页面）

启动：python app.py
默认地址：http://127.0.0.1:5000
- 接口说明：GET /apidocs
- 存活探针：GET /healthz   就绪探针：GET /readyz

HTTP 中间件统一由 http_middleware.init_app 装配（该文件顶部有说明）：
  请求 ID / 访问日志 / 安全响应头 / CORS / 限流 / Gzip 压缩 / 统一 JSON 错误

生产部署提示
------------
Flask 自带的 app.run() 只是开发服务器（单进程、无优雅退出），线上请换 WSGI：
    waitress-serve --listen=0.0.0.0:5000 app:app             # Windows 友好
    gunicorn -w 4 -k gthread --threads 8 -b :5000 app:app    # Linux
注意：Agent 的会话检查点当前存在进程内存（InMemorySaver），多 worker 时
「发起审批」和「提交审批」可能落到不同进程而找不到挂起状态，
要上多 worker 必须先换成 Redis/Postgres 版 checkpointer。
"""

from flask import Flask, jsonify, request

import config
import db
import http_middleware
import lowcode_auth
import lowcode_store
import tool_store
from attachment import MAX_UPLOAD_MB, attachment_bp, supported_summary
from conversation_api import conversation_bp
from lowcode_agent import lowcode_agent_bp
from lowcode_api import lowcode_bp
from lowcode_auth import auth_bp
from message import message_bp
from logging_setup import get_logger, setup_logging
from workspace_api import workspace_bp

# 日志要在其它模块之前初始化，否则前面 import 过程产生的日志会走默认配置
setup_logging()
log = get_logger("app")

app = Flask(__name__, static_folder=None)  # 纯后端：不提供任何静态文件
# 限制请求体大小：超过时返回 413，避免大文件把内存打满（错误文案在 http_middleware）
app.config["MAX_CONTENT_LENGTH"] = int((MAX_UPLOAD_MB + 2) * 1024 * 1024)
# 中文不转义成 \uXXXX：响应更小、日志更好读，前端解析行为不变
app.json.ensure_ascii = False
# 宽容结尾斜杠：/users 与 /users/ 都认，减少前端拼错地址的排查成本
app.url_map.strict_slashes = False

db.init_db()  # 启动时确保库表已创建
tool_store.init_tool_tables()  # 工具用的数据表 + 首次种子数据（幂等，同样启动时做一次）
lowcode_store.init_lowcode_tables()  # 低代码平台表 + 种子用户/示例页面（幂等）
lowcode_auth.init_auth_tables()  # 令牌表 + 给种子用户回填演示密码（幂等）
app.register_blueprint(message_bp)
app.register_blueprint(attachment_bp)
app.register_blueprint(workspace_bp)
app.register_blueprint(conversation_bp)
app.register_blueprint(lowcode_bp)
app.register_blueprint(lowcode_agent_bp)
app.register_blueprint(auth_bp)


def _readiness_probe() -> dict:
    """就绪探针：MySQL 连得上才算就绪。

    没选工作目录属于正常状态（用户随时可以选），不算「未就绪」。
    """
    return {"mysql": db.ping()}


# 统一装配 HTTP 中间件
http_middleware.init_app(app, readiness_probe=_readiness_probe)


# ---------------- 简易接口说明页 ----------------
@app.route("/apidocs")
def apidocs():
    return jsonify(
        {
            "接口列表": {
                "GET    /healthz": "存活探针（进程活着即 200，不查外部依赖）",
                "GET    /readyz": "就绪探针（MySQL 不通时返回 503）",
                "GET    /users": "查询全部用户（?keyword=可选，模糊搜索姓名/邮箱）",
                "GET    /users/<id>": "查询单个用户",
                "POST   /users": "新增用户，请求体 {\"name\": \"xx\", \"email\": \"xx@xx.com\"}",
                "PUT    /users/<id>": "修改用户，请求体可只传要改的字段 {\"name\": \"xx\"}",
                "DELETE /users/<id>": "删除用户",
                "POST   /attachments": "上传附件（multipart/form-data，字段名 file），返回附件 id 与解析预览",
                "GET    /attachments/<id>/raw": "读取附件原文件（图片预览/下载）",
                "DELETE /attachments/<id>": "删除附件（记录 + 磁盘文件）",
                "GET    /conversations": "会话列表（最近更新的排最前），左侧列表用",
                "POST   /conversations": "新建会话（可选 {\"title\": \"...\"}），返回会话记录",
                "GET    /conversations/<id>": "会话详情 + 全部消息，点开历史对话时回填界面",
                "PUT    /conversations/<id>": "重命名会话，请求体 {\"title\": \"...\"}",
                "DELETE /conversations/<id>": "删除会话及其全部消息",
                "POST   /conversations/<id>/messages": (
                    "会话内发消息（SSE，事件格式同 /send-message/stream）。"
                    "请求体 {\"content\": \"...\", \"attachments\": [{\"id\": \"附件id\"}]}。"
                    "历史存在服务端，前端只传本轮新消息"
                ),
                "POST   /conversations/<id>/messages/resume": (
                    "会话内提交人工审批结果并续跑（SSE）。请求体 {\"thread_id\": \"...\", "
                    "\"decisions\": [{\"type\": \"approve\"}], \"message_id\": 123}，"
                    "其中 message_id 取 interrupt 事件里带回的值"
                ),
                "POST   /send-message/stream": (
                    "流式对话（SSE）。请求体 {\"messages\": [{\"role\": \"user\", "
                    "\"content\": \"...\", \"attachments\": [{\"id\": \"附件id\"}]}]}，"
                    "可选 {\"thread_id\": \"...\"}。工具需要人工审批时会额外下发 "
                    "{\"type\": \"interrupt\", \"thread_id\": \"...\", \"actions\": [...]}"
                ),
                "POST   /send-message/resume": (
                    "提交人工审批结果并继续被挂起的对话（SSE，事件格式同 "
                    "/send-message/stream）。请求体 {\"thread_id\": \"...\", "
                    "\"decisions\": [{\"type\": \"approve\"}]}，decisions 按位置与 "
                    "interrupt 事件里的 actions 一一对应"
                ),
                "GET    /fs/dirs": (
                    "浏览服务端目录（?path= 可选，留空从用户主目录开始）。"
                    "后端没有图形界面时的备选方案，替代 /workspace/pick"
                ),
                "POST   /workspace/pick": (
                    "在服务端所在机器上弹出系统「选择文件夹」对话框，选中即设为"
                    "工作目录（推荐）。会阻塞到用户选完；取消时返回 "
                    "{\"cancelled\": true}。请求体可传 {\"initial_dir\": \"...\"}"
                ),
                "GET    /workspace": "当前工作目录 + 工作区文件清单",
                "POST   /workspace": (
                    "设置工作目录（即允许 Agent 读写代码的文件夹），"
                    "请求体 {\"path\": \"D:\\\\code\\\\demo\"}"
                ),
                "GET    /workspace/file": (
                    "预览工作区内的文本文件（?path= 相对工作目录的路径）"
                ),
            },
            "附件支持": supported_summary(),
            "限流": {
                "普通接口": f"{config.RATE_LIMIT_DEFAULT_PER_MIN} 次/分钟/IP",
                "对话接口": f"{config.RATE_LIMIT_SSE_PER_MIN} 次/分钟/IP",
                "上传接口": f"{config.RATE_LIMIT_UPLOAD_PER_MIN} 次/分钟/IP",
                "开关": config.RATE_LIMIT_ENABLED,
            },
            "运行环境": {"APP_ENV": config.APP_ENV, "debug": config.DEBUG},
            "示例": "curl http://127.0.0.1:5000/users",
        }
    )


# ---------------- 增 ---------------- 
@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    if not name or not email:
        return jsonify({"error": "name 和 email 均为必填"}), 400
    try:
        user = db.create_user(name, email)
    except Exception as exc:  # 唯一约束冲突（邮箱重复）等
        return jsonify({"error": f"创建失败：{exc}"}), 400
    return jsonify(user), 201


# ---------------- 查 ----------------
@app.route("/users", methods=["GET"])
def list_users():
    keyword = (request.args.get("keyword") or "").strip()
    return jsonify(db.list_users(keyword))


@app.route("/users/<string:user_id>", methods=["GET"])
def get_user(user_id):
    user = db.get_user(user_id)
    if user is None:
        return jsonify({"error": f"用户 {user_id} 不存在"}), 404
    return jsonify(user)


# ---------------- 改 ----------------
@app.route("/users/<string:user_id>", methods=["PUT"])
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or None
    email = (data.get("email") or "").strip() or None
    if name is None and email is None:
        return jsonify({"error": "请求体至少包含 name 或 email 之一"}), 400
    try:
        user = db.update_user(user_id, name=name, email=email)
    except Exception as exc:
        return jsonify({"error": f"修改失败：{exc}"}), 400
    if user is None:
        return jsonify({"error": f"用户 {user_id} 不存在"}), 404
    return jsonify(user)


# ---------------- 删 ----------------
@app.route("/users/<string:user_id>", methods=["DELETE"])
def delete_user(user_id):
    if not db.delete_user(user_id):
        return jsonify({"error": f"用户 {user_id} 不存在"}), 404
    return jsonify({"message": f"用户 {user_id} 已删除"})


if __name__ == "__main__":
    if config.IS_PROD:
        log.warning(
            "APP_ENV=prod 但用的是 Flask 开发服务器，请改用 waitress/gunicorn 启动"
        )
    log.info(
        "serving on http://%s:%s (env=%s, debug=%s, cors=%s)",
        config.HOST,
        config.PORT,
        config.APP_ENV,
        config.DEBUG,
        ",".join(config.CORS_ORIGINS),
    )
    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
        threaded=config.THREADED,  # SSE 长连接必须多线程
    )
