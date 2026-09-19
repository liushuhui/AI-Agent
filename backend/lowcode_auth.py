# -*- coding: utf-8 -*-
"""低代码平台的鉴权层：登录 / 登出 / 令牌校验（替代原来的 X-User-Id 占位实现）。

- 密码：PBKDF2-HMAC-SHA256（标准库，无新依赖），库存 "salt$hash"；
- 令牌：随机字节串存 MySQL `lowcode_token` 表（服务端会话，可随时吊销），
  有效期 7 天；请求头 `Authorization: Bearer <token>`；
- 演示账号（admin/admin123、alice/alice123、bob/bob123）在首次初始化时写入，
  生产环境请补注册/改密流程并清掉这些种子密码。

数据库相关函数与 HTTP 端点放在同一个文件里，篇幅小、不用来回跳。
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

import db
from lowcode_store import Forbidden, LowcodeError, Unauthorized

PBKDF2_ROUNDS = 120_000
TOKEN_TTL_HOURS = 24 * 7

# 首次初始化时写入的演示账号（用户名 -> 初始密码）；已存在密码的用户不会覆盖
SEED_PASSWORDS = {"admin": "admin123", "alice": "alice123", "bob": "bob123"}

_TOKEN_DDL = """
    CREATE TABLE IF NOT EXISTS lowcode_token (
        token      VARCHAR(64) NOT NULL,
        user_id    VARCHAR(64) NOT NULL,
        created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME    NOT NULL,
        PRIMARY KEY (token),
        KEY idx_lowcode_token_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录令牌（服务端会话）'
"""


# ---------------- 密码 ----------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    ).hex()
    return hmac.compare_digest(check, digest)


def _ensure_column(cur, table: str, column: str, ddl: str) -> None:
    """给已存在的表补列（幂等；MySQL 没有 ADD COLUMN IF NOT EXISTS）。"""
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (db.MYSQL_CONFIG["database"], table, column),
    )
    if int(cur.fetchone()["n"]) == 0:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")


def init_auth_tables() -> None:
    """建令牌表 + 给老的 lowcode_user 补 password_hash 并回填演示密码（幂等）。"""
    with db.db_session() as cur:
        cur.execute(_TOKEN_DDL)
        _ensure_column(cur, "lowcode_user", "password_hash", "VARCHAR(255) NOT NULL DEFAULT ''")
        for username, password in SEED_PASSWORDS.items():
            cur.execute(
                "UPDATE lowcode_user SET password_hash = %s "
                "WHERE id = %s AND (password_hash IS NULL OR password_hash = '')",
                (hash_password(password), username),
            )
        cur.execute("DELETE FROM lowcode_token WHERE expires_at <= NOW()")


# ---------------- 令牌 ----------------

def login(username: str, password: str) -> dict:
    """校验账号密码并签发令牌；失败一律抛 401（不区分用户名/密码错）。"""
    with db.db_session() as cur:
        cur.execute(
            "SELECT id, name, role, password_hash FROM lowcode_user WHERE id = %s",
            (username,),
        )
        row = cur.fetchone()
    if row is None or not verify_password(password, row.get("password_hash") or ""):
        raise Unauthorized("用户名或密码不正确")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=TOKEN_TTL_HOURS)
    with db.db_session() as cur:
        cur.execute(
            "INSERT INTO lowcode_token (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, row["id"], expires_at),
        )
    return {
        "token": token,
        "user": {"id": row["id"], "name": row["name"], "role": row["role"]},
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def revoke_token(token: str) -> None:
    if not token:
        return
    with db.db_session() as cur:
        cur.execute("DELETE FROM lowcode_token WHERE token = %s", (token,))


def resolve_token(token: str) -> dict | None:
    """令牌 -> 用户；无效/过期返回 None。"""
    if not token:
        return None
    with db.db_session() as cur:
        cur.execute(
            "SELECT u.id, u.name, u.role FROM lowcode_token t "
            "JOIN lowcode_user u ON u.id = t.user_id "
            "WHERE t.token = %s AND t.expires_at > NOW()",
            (token,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "role": row["role"]}


# ---------------- HTTP 端点 + 统一鉴权助手 ----------------

auth_bp = Blueprint("auth", __name__)


@auth_bp.errorhandler(LowcodeError)
def _on_auth_error(exc: LowcodeError):
    return jsonify({"error": str(exc)}), exc.code


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def require_user() -> dict:
    """其余 blueprint 统一用它取当前用户；无效令牌抛 401。"""
    user = resolve_token(_bearer_token())
    if user is None:
        raise Unauthorized("未登录或登录已过期")
    return user


def can_access(user: dict, owner_id: str) -> bool:
    """资源归属判断：管理员放行；其余要求 owner 一致（空 owner 的历史数据仅管理员可见）。"""
    return user["role"] == "admin" or (bool(owner_id) and owner_id == user["id"])


@auth_bp.post("/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise LowcodeError("用户名和密码必填")
    return jsonify(login(username, password))


@auth_bp.post("/auth/logout")
def api_logout():
    revoke_token(_bearer_token())
    return jsonify({"ok": True})


@auth_bp.get("/auth/me")
def api_me():
    return jsonify({"user": require_user()})


@auth_bp.get("/auth/users")
def api_users():
    """用户清单（仍供前端展示/调试用）——仅管理员可见。"""
    user = require_user()
    if user["role"] != "admin":
        raise Forbidden("只有管理员可以查看用户清单")
    import lowcode_store as store

    return jsonify({"users": store.list_users()})
