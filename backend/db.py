# -*- coding: utf-8 -*-
"""
db.py —— MySQL 连接层 + 增删改查(CRUD)逻辑

依赖：pip install pymysql
如需覆盖默认值，可设置环境变量 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。
"""

import json
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

# 本模块在 import 期（下面 MYSQL_CONFIG）就要读环境变量，
# 所以必须在这里先加载 .env —— load_dotenv 只对「它之后」执行的 os.getenv 生效，
# 加载晚一步就读不到 .env 里的值。谁读环境变量，谁负责先加载。
load_dotenv(override=True)

# ---------------- 配置 ----------------
MYSQL_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "123456"),
    "database": os.getenv("DB_NAME", "python"),
    "charset": "utf8mb4",
    # 超时三件套：没有它们的话，数据库卡住时请求会一直挂着，
    # 线程池被慢慢占满（Flask 自带服务器是多线程的）。
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "5")),
    "read_timeout": int(os.getenv("DB_READ_TIMEOUT", "15")),
    "write_timeout": int(os.getenv("DB_WRITE_TIMEOUT", "15")),
}

# ---------------- 连接 ----------------


def get_connection() -> pymysql.connections.Connection:
    """获取 MySQL 连接（DictCursor：查询结果按列名取值）。"""
    return pymysql.connect(**MYSQL_CONFIG, cursorclass=DictCursor)


def ping(timeout: int = 2) -> str:
    """探活用：能查通就返回版本号。给 /readyz 就绪探针用。

    刻意用独立（更短）的 connect_timeout：探针必须快速给出结论，
    不能让负载均衡一直等。
    """
    budget = max(1, min(timeout, MYSQL_CONFIG["connect_timeout"]))
    conn = pymysql.connect(
        **{**MYSQL_CONFIG, "connect_timeout": budget}, cursorclass=DictCursor
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS version")
            row = cur.fetchone() or {}
        return str(row.get("version", "unknown"))
    finally:
        conn.close()


def _connect_without_database() -> pymysql.connections.Connection:
    """连接 MySQL 但不指定 database，用于首次建库。"""
    config = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    return pymysql.connect(**config, cursorclass=DictCursor)


@contextmanager
def db_session():
    """事务上下文：正常提交、异常回滚，用后自动关闭连接；产出字典游标。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """确保数据库与 users 表存在（幂等）。启动服务时调用一次。"""
    # 1) 建库（MySQL 中库不存在时，带 database 的连接会直接报错）
    conn = _connect_without_database()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        conn.commit()
    finally:
        conn.close()

    # 2) 建表：id 为字符串主键（UUID）。注意 MySQL 不允许 VARCHAR 使用 AUTO_INCREMENT
    with db_session() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         VARCHAR(36)  NOT NULL,
                name       VARCHAR(100) NOT NULL,
                email      VARCHAR(255) NOT NULL UNIQUE,
                created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # 附件表：text_content 存解析出的正文，图片类为空串
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
                id           VARCHAR(36)  NOT NULL,
                name         VARCHAR(255) NOT NULL,
                stored_name  VARCHAR(255) NOT NULL,
                mime         VARCHAR(128) NOT NULL DEFAULT '',
                size         INT UNSIGNED NOT NULL DEFAULT 0,
                kind         VARCHAR(16)  NOT NULL DEFAULT 'other',
                status       VARCHAR(16)  NOT NULL DEFAULT 'ready',
                error        TEXT         NULL,
                text_content LONGTEXT     NULL,
                created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_attachments_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # 会话表：一次「打开新对话」就是一行。
        # updated_at 用来给会话列表排序（最近聊过的排最前），
        # 每次追加消息都会一起刷新，所以列表不用再 JOIN 消息表。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id            VARCHAR(36)  NOT NULL,
                title         VARCHAR(120) NOT NULL DEFAULT '新对话',
                owner_id      VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '归属用户；空 = 历史遗留，仅管理员可见',
                message_count INT UNSIGNED NOT NULL DEFAULT 0,
                created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_conversations_updated_at (updated_at),
                KEY idx_conversations_owner (owner_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # 消息表：会话的「显示历史」= 送给模型的上下文，二者共用一份，避免两处不一致。
        # attachments 存 JSON 数组（附件引用），不存文件本身；
        # reasoning 存思维链，重新打开老会话时还能看到「当时怎么想的」。
        # thread_id 记录这一轮用的 LangGraph 线程：挂起中的工具调用、待办清单
        # 只存在 checkpointer 里，删会话时靠它把对应线程一并清掉（老数据为空）。
        # 用自增 BIGINT 做排序键：同一秒内也能保证顺序稳定。
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                conversation_id VARCHAR(36)  NOT NULL,
                role            VARCHAR(16)  NOT NULL,
                content         LONGTEXT     NOT NULL,
                reasoning       LONGTEXT     NULL,
                attachments     LONGTEXT     NULL,
                thread_id       VARCHAR(64)  NULL,
                created_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                PRIMARY KEY (id),
                KEY idx_conv_messages (conversation_id, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )

        # 建表语句只对新库生效，已经建过表的库在这里补新列（幂等）
        _ensure_column(cur, "conversation_messages", "thread_id", "VARCHAR(64) NULL")
        # 会话归属：老库里的历史会话没有主，只对管理员可见
        _ensure_column(cur, "conversations", "owner_id", "VARCHAR(64) NOT NULL DEFAULT ''")


def _ensure_column(cur, table: str, column: str, ddl: str) -> None:
    """给已存在的表补列（MySQL 不支持 ADD COLUMN IF NOT EXISTS）。

    先查 information_schema 再决定要不要 ALTER，所以每次启动跑一遍是安全的：
    列已存在时什么都不做。这样加新列不用手动改库。
    """
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
        (MYSQL_CONFIG["database"], table, column),
    )
    if int(cur.fetchone()["n"]) == 0:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")


# ---------------- 增删改查（CRUD） ----------------

def _row_to_dict(row) -> dict | None:
    """DictCursor 已返回 dict，这里把 datetime 统一转成字符串便于 JSON 输出。"""
    if row is None:
        return None
    value = row.get("created_at")
    if isinstance(value, (datetime, date)):
        row["created_at"] = value.strftime("%Y-%m-%d %H:%M:%S")
    return row


def create_user(name: str, email: str) -> dict:
    """新增：id 由 Python 生成 UUID 字符串，返回完整记录。"""
    new_id = str(uuid.uuid4())
    with db_session() as cur:
        cur.execute(
            "INSERT INTO users (id, name, email) VALUES (%s, %s, %s)",
            (new_id, name, email),
        )
    return get_user(new_id)


def get_user(user_id: str) -> dict | None:
    """查询单条：按字符串主键读取，不存在返回 None。"""
    with db_session() as cur:
        cur.execute(
            "SELECT id, name, email, created_at FROM users WHERE id = %s", (user_id,)
        )
        row = cur.fetchone()
    return _row_to_dict(row)


def list_users(keyword: str = "") -> list:
    """查询列表：支持按 name/email 模糊搜索；keyword 为空返回全部。"""
    with db_session() as cur:
        if keyword:
            like = f"%{keyword}%"
            cur.execute(
                "SELECT id, name, email, created_at FROM users "
                "WHERE name LIKE %s OR email LIKE %s "
                "ORDER BY created_at DESC, id DESC",
                (like, like),
            )
        else:
            cur.execute(
                "SELECT id, name, email, created_at FROM users "
                "ORDER BY created_at DESC, id DESC"
            )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def update_user(user_id: str, name: str | None = None, email: str | None = None) -> dict | None:
    """修改：仅更新传入的非空字段，返回更新后的记录；记录不存在返回 None。"""
    with db_session() as cur:
        cur.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row is None:
            return None
        new_name = name if name is not None else row["name"]
        new_email = email if email is not None else row["email"]
        cur.execute(
            "UPDATE users SET name = %s, email = %s WHERE id = %s",
            (new_name, new_email, user_id),
        )
    return get_user(user_id)


def delete_user(user_id: str) -> bool:
    """删除：按字符串主键删除，返回是否删除成功。"""
    with db_session() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        affected = cur.rowcount
    return affected > 0


# ---------------- 附件 CRUD ----------------

_ATTACHMENT_COLUMNS = (
    "id, name, stored_name, mime, size, kind, status, error, text_content, created_at"
)


def _attachment_row(row) -> dict | None:
    """把附件行转成便于 JSON 输出的字典（时间转字符串，解析文本按需取用）。"""
    if row is None:
        return None
    value = row.get("created_at")
    if isinstance(value, (datetime, date)):
        row["created_at"] = value.strftime("%Y-%m-%d %H:%M:%S")
    return row


def create_attachment(
    name: str,
    stored_name: str,
    mime: str = "",
    size: int = 0,
    kind: str = "other",
    status: str = "ready",
    error: str | None = None,
    text_content: str | None = None,
) -> dict:
    """新增附件记录，返回完整记录（含解析文本，供入模使用）。"""
    new_id = str(uuid.uuid4())
    with db_session() as cur:
        cur.execute(
            "INSERT INTO attachments "
            "(id, name, stored_name, mime, size, kind, status, error, text_content) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (new_id, name, stored_name, mime, size, kind, status, error, text_content),
        )
    return get_attachment(new_id)


def get_attachment(attachment_id: str) -> dict | None:
    """查询单个附件，不存在返回 None。"""
    with db_session() as cur:
        cur.execute(
            f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE id = %s",
            (attachment_id,),
        )
        row = cur.fetchone()
    return _attachment_row(row)


def get_attachments(ids: list) -> dict:
    """批量查询：返回 {id: 记录} 字典，顺序无关；空入参返回空字典。"""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    with db_session() as cur:
        cur.execute(
            f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE id IN ({placeholders})",
            tuple(ids),
        )
        rows = cur.fetchall()
    return {r["id"]: _attachment_row(r) for r in rows}


def delete_attachment(attachment_id: str) -> bool:
    """删除附件记录，返回是否删除成功（磁盘文件由调用方处理）。"""
    with db_session() as cur:
        cur.execute("DELETE FROM attachments WHERE id = %s", (attachment_id,))
        affected = cur.rowcount
    return affected > 0


def list_expired_attachments(before: str) -> list:
    """列出 before 之前创建的附件，用于过期清理。before 形如 '2026-09-01 00:00:00'。"""
    with db_session() as cur:
        cur.execute(
            f"SELECT {_ATTACHMENT_COLUMNS} FROM attachments WHERE created_at < %s",
            (before,),
        )
        rows = cur.fetchall()
    return [_attachment_row(r) for r in rows]


# ---------------- 会话（conversations）----------------
#
# 会话的「显示历史」和「送给模型的上下文」共用同一份数据（conversation_messages）。
# 这样只有一处真相：前端刷新、换设备、重新打开老会话，看到的都是同一份记录，
# 也不会出现「界面上有、模型看不到」或反过来的一致性问题。

_CONVERSATION_COLUMNS = "id, title, owner_id, message_count, created_at, updated_at"
# 带上 conversation_id：续跑审批时要校验「这条消息确实属于这个会话」
_MESSAGE_COLUMNS = (
    "id, conversation_id, role, content, reasoning, attachments, thread_id, created_at"
)

# 会话标题自动生成时最多取几个字（再长列表里也显示不下）
TITLE_MAX_CHARS = 24
# 新建会话的默认标题：标题还挂着它，说明谁都没起过名字，
# 第一条带文字的消息可以顺手续上
DEFAULT_TITLE = "新对话"


def _format_time(value):
    """datetime → 字符串，前端直接展示，也便于 JSON 序列化。"""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _conversation_row(row) -> dict | None:
    if row is None:
        return None
    row["created_at"] = _format_time(row.get("created_at"))
    row["updated_at"] = _format_time(row.get("updated_at"))
    return row


def _message_row(row) -> dict | None:
    """消息行 → 字典；attachments 是 JSON 文本，这里解回列表。"""
    if row is None:
        return None
    row["created_at"] = _format_time(row.get("created_at"))
    raw = row.get("attachments")
    if isinstance(raw, str):
        try:
            row["attachments"] = json.loads(raw)
        except ValueError:
            row["attachments"] = []
    elif raw is None:
        row["attachments"] = []
    return row


def make_title(text: str) -> str:
    """用第一条用户消息生成会话标题：压平空白 + 截断。"""
    flat = " ".join((text or "").split())
    if not flat:
        return DEFAULT_TITLE
    return flat[:TITLE_MAX_CHARS] + ("…" if len(flat) > TITLE_MAX_CHARS else "")


def create_conversation(title: str = DEFAULT_TITLE, owner_id: str = "") -> dict:
    """新建会话（带归属用户），返回完整记录。"""
    new_id = str(uuid.uuid4())
    with db_session() as cur:
        cur.execute(
            "INSERT INTO conversations (id, title, owner_id) VALUES (%s, %s, %s)",
            (new_id, title, owner_id),
        )
    return get_conversation(new_id)


def get_conversation(conversation_id: str) -> dict | None:
    """查询单个会话，不存在返回 None。"""
    with db_session() as cur:
        cur.execute(
            f"SELECT {_CONVERSATION_COLUMNS} FROM conversations WHERE id = %s",
            (conversation_id,),
        )
        row = cur.fetchone()
    return _conversation_row(row)


def list_conversations(limit: int = 200, owner_id: str | None = None) -> list:
    """会话列表，最近有更新的排最前；owner_id 非空时只看该用户的会话。"""
    sql = f"SELECT {_CONVERSATION_COLUMNS} FROM conversations"
    params: list = []
    if owner_id:
        sql += " WHERE owner_id = %s"
        params.append(owner_id)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT %s"
    params.append(int(limit))
    with db_session() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [_conversation_row(r) for r in rows]


def rename_conversation(conversation_id: str, title: str) -> dict | None:
    """改标题，返回更新后的记录；会话不存在返回 None。"""
    with db_session() as cur:
        cur.execute(
            "UPDATE conversations SET title = %s WHERE id = %s",
            (title, conversation_id),
        )
        if cur.rowcount == 0:
            # rowcount 为 0 有两种可能：会话不存在，或者标题没变化。
            # 先查一次再决定是不是 404（标题没变时仍然算成功）。
            cur.execute(
                "SELECT id FROM conversations WHERE id = %s", (conversation_id,)
            )
            if cur.fetchone() is None:
                return None
    return get_conversation(conversation_id)


def delete_conversation(conversation_id: str) -> bool:
    """删除会话及其全部消息，返回是否删除成功。"""
    with db_session() as cur:
        # 先删消息再删会话：表里没建外键，避免有人手动建库时顺序不对留下孤儿数据
        cur.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = %s",
            (conversation_id,),
        )
        cur.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
        affected = cur.rowcount
    return affected > 0


def add_message(
    conversation_id: str,
    role: str,
    content: str = "",
    reasoning: str | None = None,
    attachments: list | None = None,
    thread_id: str | None = None,
) -> dict:
    """追加一条消息，并同时刷新会话的 updated_at / message_count。

    这两件事放在同一个事务里：会话列表的排序依赖 updated_at，
    不能出现「消息写进去了但列表顺序没更新」的中间态。

    thread_id 只给助手消息填（这一轮用的 LangGraph 线程），
    删会话时靠它把 checkpointer 里还挂着的状态一起清掉。
    """
    payload = json.dumps(attachments, ensure_ascii=False) if attachments else None
    with db_session() as cur:
        cur.execute(
            "INSERT INTO conversation_messages "
            "(conversation_id, role, content, reasoning, attachments, thread_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (conversation_id, role, content, reasoning, payload, thread_id),
        )
        message_id = cur.lastrowid
        cur.execute(
            "UPDATE conversations "
            "SET message_count = message_count + 1, updated_at = NOW() "
            "WHERE id = %s",
            (conversation_id,),
        )
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "reasoning": reasoning,
        "attachments": attachments or [],
        "thread_id": thread_id,
    }


def get_message(message_id: int) -> dict | None:
    """按 id 查一条消息（续跑审批前拿它校验「消息确实属于这个会话」）。"""
    with db_session() as cur:
        cur.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM conversation_messages WHERE id = %s",
            (message_id,),
        )
        row = cur.fetchone()
    return _message_row(row)


def append_message(message_id: int, text: str, reasoning: str = "") -> None:
    """把流式生成的内容追加到某条消息上（挂起时、结束时各调用一次）。

    追加而不是覆盖：一轮助手回复可能被人工审批中断多次，每次续跑产出的都是
    「同一条回复的后半段」。用 CONCAT 在 SQL 里拼接，不依赖「先读后写」，
    也就没有并发覆盖的问题；同时刷新会话的 updated_at，让列表排序体现最新活动。

    换句话说，即使生成途中用户刷新页面 / 点了停止，已经生成的内容也已经落库。
    """
    with db_session() as cur:
        cur.execute(
            "UPDATE conversation_messages "
            "SET content = CONCAT(content, %s), "
            "reasoning = CONCAT(COALESCE(reasoning, ''), %s) "
            "WHERE id = %s",
            (text, reasoning, message_id),
        )
        cur.execute(
            "UPDATE conversations SET updated_at = NOW() "
            "WHERE id = (SELECT conversation_id FROM conversation_messages WHERE id = %s)",
            (message_id,),
        )


def list_messages(conversation_id: str, limit: int = 500) -> list:
    """按时间正序列出会话的全部消息（limit 只是防御性上限，正常会话远到不了）。"""
    with db_session() as cur:
        cur.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM conversation_messages "
            "WHERE conversation_id = %s ORDER BY id ASC LIMIT %s",
            (conversation_id, int(limit)),
        )
        rows = cur.fetchall()
    return [_message_row(r) for r in rows]


def list_thread_ids(conversation_id: str) -> list:
    """这个会话用过的全部 LangGraph 线程 id（删会话时据此清理挂起状态）。

    一轮对话就是一个线程，正常跑完的线程已经释放，重复清是安全的；
    真正要清的是「挂起后没人续跑」的那些。
    """
    with db_session() as cur:
        cur.execute(
            "SELECT DISTINCT thread_id FROM conversation_messages "
            "WHERE conversation_id = %s AND thread_id IS NOT NULL",
            (conversation_id,),
        )
        rows = cur.fetchall()
    return [row["thread_id"] for row in rows]
