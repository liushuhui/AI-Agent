# -*- coding: utf-8 -*-
"""lowcode_store.py —— 低代码平台的存储层（页面 / 发布版本 / 用户）。

沿用 tool_store.py 的风格：建表语句集中放常量，启动时 init 一次（幂等）。

权限模型（占位实现，等接入真正的登录后只需要改 lowcode_api._user 的取数来源）：
- admin   ：可读写所有页面
- editor  ：可读写自己的页面，可查看公开页面
- viewer  ：只读（自己的 + 公开的），不能新建/修改/删除

当前用户由请求头 X-User-Id 指定（缺省 admin，本机开发方便）。
"""

import json
import uuid
from datetime import datetime

import db
from lowcode_schema import MAX_SCHEMA_BYTES, default_schema, sample_schema, validate_schema


# ---------------- 业务异常体系 ----------------
# 所有存储层错误都抛 LowcodeError 子类，由各 blueprint 的 errorhandler 统一翻成
# {"error": msg} + HTTP code；code 字段直接当状态码用。

class LowcodeError(Exception):
    """业务错误：api 层统一翻成 {"error": ...} + 对应状态码。"""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


class NotFound(LowcodeError):
    """404：资源不存在。"""

    def __init__(self, message: str = "页面不存在"):
        super().__init__(message, 404)


class Forbidden(LowcodeError):
    """403：已登录但无权操作（RBAC 校验不通过）。"""

    def __init__(self, message: str = "没有权限执行该操作"):
        super().__init__(message, 403)


class Conflict(LowcodeError):
    """409：乐观锁版本冲突（base_version 与库中当前版本不一致）。"""

    def __init__(self, message: str = "版本冲突"):
        super().__init__(message, 409)


class Unauthorized(LowcodeError):
    """401：未登录或令牌失效（由 lowcode_auth 抛出/抛出）。"""

    def __init__(self, message: str = "未登录或登录已过期"):
        super().__init__(message, 401)


# ---------------- 建表 DDL（启动时幂等执行一次） ----------------
# 三张表：页面主表 / 发布快照表 / 用户表。
# lowcode_page 同时存「当前草稿」和乐观锁 version；
# lowcode_page_version 只在 publish 时写一行快照，供历史查看与回滚。

_PAGE_DDL = """
    CREATE TABLE IF NOT EXISTS lowcode_page (
        id          VARCHAR(40)   NOT NULL COMMENT '页面 id',
        name        VARCHAR(128)  NOT NULL COMMENT '页面名',
        description VARCHAR(512)  NOT NULL DEFAULT '' COMMENT '备注',
        visibility  VARCHAR(16)   NOT NULL DEFAULT 'private' COMMENT 'private/public',
        owner_id    VARCHAR(64)   NOT NULL COMMENT '负责人',
        schema_json LONGTEXT      NOT NULL COMMENT '页面 Schema（JSON 文本）',
        version     INT           NOT NULL DEFAULT 1 COMMENT '乐观锁版本号',
        status      VARCHAR(16)   NOT NULL DEFAULT 'draft' COMMENT 'draft/published',
        created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_lowcode_owner (owner_id),
        KEY idx_lowcode_updated (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='低代码页面'
"""

_VERSION_DDL = """
    CREATE TABLE IF NOT EXISTS lowcode_page_version (
        page_id     VARCHAR(40)  NOT NULL,
        version     INT          NOT NULL,
        schema_json LONGTEXT     NOT NULL,
        comment     VARCHAR(255) NOT NULL DEFAULT '',
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (page_id, version)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='发布快照（可回滚）'
"""

_USER_DDL = """
    CREATE TABLE IF NOT EXISTS lowcode_user (
        id   VARCHAR(64) NOT NULL COMMENT '用户 id',
        name VARCHAR(64) NOT NULL COMMENT '显示名',
        role VARCHAR(16) NOT NULL DEFAULT 'editor' COMMENT 'admin/editor/viewer',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='低代码用户（占位权限模型）'
"""

# 种子用户：admin 全能；alice 编辑；bob 只读（切换 X-User-Id 就能演示权限差异）
_SEED_USERS = (
    ("admin", "管理员", "admin"),
    ("alice", "Alice（编辑）", "editor"),
    ("bob", "Bob（只读）", "viewer"),
)


def init_lowcode_tables() -> None:
    """建表 + 首次灌种子数据（用户、示例页面），幂等，启动时调用一次。

    幂等靠「先查 count 再决定插不插」：表为空才灌种子，已有数据绝不覆盖。
    """
    with db.db_session() as cur:
        cur.execute(_PAGE_DDL)
        cur.execute(_VERSION_DDL)
        cur.execute(_USER_DDL)

        # 空表才灌种子用户，方便直接用三个角色演示权限差异
        cur.execute("SELECT COUNT(*) AS n FROM lowcode_user")
        if int(cur.fetchone()["n"]) == 0:
            for uid, name, role in _SEED_USERS:
                cur.execute(
                    "INSERT INTO lowcode_user (id, name, role) VALUES (%s, %s, %s)",
                    (uid, name, role),
                )

        # 空表才内置一个公开的示例页面（直接 published，开箱即见）
        cur.execute("SELECT COUNT(*) AS n FROM lowcode_page")
        if int(cur.fetchone()["n"]) == 0:
            schema = sample_schema()
            cur.execute(
                "INSERT INTO lowcode_page "
                "(id, name, description, visibility, owner_id, schema_json, version, status) "
                "VALUES (%s, %s, %s, 'public', 'admin', %s, 1, 'published')",
                (
                    # uuid 取前 12 位做短 id，列表/URL 里更短，碰撞概率可忽略
                    uuid.uuid4().hex[:12],
                    schema["name"],
                    "内置示例：绑定 GET /users",
                    json.dumps(schema, ensure_ascii=False),
                ),
            )


# ---------------- 用户 ----------------

def get_user(user_id: str) -> dict | None:
    """按 id 取用户（含 role）；不存在返回 None。"""
    with db.db_session() as cur:
        cur.execute("SELECT id, name, role FROM lowcode_user WHERE id = %s", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    """用户清单（管理/调试用，按 id 排序）。"""
    with db.db_session() as cur:
        cur.execute("SELECT id, name, role FROM lowcode_user ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


# ---------------- 内部工具 ----------------

def _dt(value) -> str:
    """datetime 统一格式化为 'YYYY-MM-DD HH:MM:SS'；空值给空串，便于 JSON 输出。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else str(value or "")


def _row_to_page(row: dict, with_schema: bool = True) -> dict:
    """把一行 DB 记录转成对外的页面字典。

    with_schema=False 时（列表页）不反解 schema_json，省一次大 JSON 解析；
    schema 损坏时退化成默认空卡片而不是让整个列表 500。
    """
    page = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "visibility": row["visibility"],
        "owner_id": row["owner_id"],
        "status": row["status"],
        "version": int(row["version"]),
        "created_at": _dt(row.get("created_at")),
        "updated_at": _dt(row.get("updated_at")),
    }
    if with_schema:
        try:
            page["schema"] = json.loads(row["schema_json"])
        except (TypeError, ValueError):
            # 库里 schema_json 坏了：兜底给个默认空页，不让读接口崩
            page["schema"] = default_schema(row["name"])
    return page


def _can_read(user: dict, row: dict) -> bool:
    """读权限：管理员全见；或自己是负责人；或页面公开（public）。"""
    return (
        user["role"] == "admin"
        or row["owner_id"] == user["id"]
        or row["visibility"] == "public"
    )


def _can_write(user: dict, row: dict) -> bool:
    """写权限：viewer 一律只读；其余管理员全写，否则只有负责人能写。"""
    if user["role"] == "viewer":
        return False
    return user["role"] == "admin" or row["owner_id"] == user["id"]


def _fetch_row(cur, page_id: str) -> dict | None:
    """按 id 取整行页面（含 schema_json 全文）；不存在返回 None。"""
    cur.execute("SELECT * FROM lowcode_page WHERE id = %s", (page_id,))
    return cur.fetchone()


def _check_schema_size(schema: dict) -> None:
    """落库前的体积闸门：超过 MAX_SCHEMA_BYTES 直接 400，不进 DB。"""
    if len(json.dumps(schema, ensure_ascii=False)) > MAX_SCHEMA_BYTES:
        raise LowcodeError(f"schema 体积超过上限（{MAX_SCHEMA_BYTES // 1024} KB）")


# ---------------- 页面 CRUD ----------------

def list_pages(user: dict) -> list[dict]:
    """当前用户可见的页面列表（不带 schema 全文，只取摘要字段）。

    管理员看全部；非管理员只能看到「自己负责的 + 公开的」。
    按最近更新时间倒序，列表天然把最近动过的排前面。
    """
    sql = (
        "SELECT id, name, description, visibility, owner_id, status, version, "
        "created_at, updated_at FROM lowcode_page"
    )
    params: tuple = ()
    if user["role"] != "admin":
        # 非管理员过滤：本人是负责人，或页面 public
        sql += " WHERE owner_id = %s OR visibility = 'public'"
        params = (user["id"],)
    sql += " ORDER BY updated_at DESC"
    with db.db_session() as cur:
        cur.execute(sql, params)
        return [_row_to_page(r, with_schema=False) for r in cur.fetchall()]


def get_page(page_id: str, user: dict) -> dict:
    """取单个页面完整内容（含 schema）；不存在 404，无权读 403。"""
    with db.db_session() as cur:
        row = _fetch_row(cur, page_id)
    if row is None:
        raise NotFound()
    if not _can_read(user, row):
        raise Forbidden("没有权限查看该页面")
    return _row_to_page(row)


def create_page(user: dict, name: str, description: str = "", visibility: str = "private") -> dict:
    """新建草稿页：viewer 禁止；owner 自动记为当前用户；初始版本=1、状态=draft。"""
    if user["role"] == "viewer":
        raise Forbidden("只读用户不能新建页面")
    schema = default_schema(name)
    page_id = uuid.uuid4().hex[:12]
    with db.db_session() as cur:
        cur.execute(
            "INSERT INTO lowcode_page "
            "(id, name, description, visibility, owner_id, schema_json, version, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1, 'draft')",
            (
                page_id,
                name,
                description,
                # visibility 容错：非 "public" 一律按 private，防前端乱传
                "public" if visibility == "public" else "private",
                user["id"],
                json.dumps(schema, ensure_ascii=False),
            ),
        )
    return get_page(page_id, user)


def update_page(
    page_id: str,
    user: dict,
    schema: dict | None = None,
    name: str | None = None,
    visibility: str | None = None,
    base_version: int | None = None,
) -> dict:
    """局部更新页面：schema / name / visibility 三者按需传，至少传一个。

    乐观锁：前端提交时带上它编辑时看到的 base_version；若与库中当前 version
    不一致说明期间被别人改了，直接 409，让前端刷新后重试，避免覆盖他人修改。
    每次成功更新都 version+1（这就是乐观锁的版本号自增）。
    """
    if schema is None and name is None and visibility is None:
        raise LowcodeError("没有需要更新的内容")
    # 传了 schema 就先过体积闸门 + 结构校验，坏数据不进库
    if schema is not None:
        _check_schema_size(schema)
        errors = validate_schema(schema)
        if errors:
            raise LowcodeError("schema 校验失败：" + "；".join(errors[:5]))

    with db.db_session() as cur:
        row = _fetch_row(cur, page_id)
        if row is None:
            raise NotFound()
        if not _can_write(user, row):
            raise Forbidden("没有权限修改该页面")
        # 乐观锁比对：base_version 与库中 version 不符 -> 409 Conflict
        if base_version is not None and int(base_version) != int(row["version"]):
            raise Conflict(
                f"页面已被修改（库中版本 {row['version']}，提交版本 {base_version}），请刷新后重试"
            )
        # 动态拼 SET 子句：只更新本次传了的字段，避免把没传的字段写成空
        fields, params = [], []
        if schema is not None:
            fields.append("schema_json = %s")
            params.append(json.dumps(schema, ensure_ascii=False))
        if name is not None:
            fields.append("name = %s")
            params.append(name)
        if visibility is not None:
            fields.append("visibility = %s")
            params.append("public" if visibility == "public" else "private")
        # 无论改了哪个字段，版本号都 +1，供下次乐观锁比对
        fields.append("version = version + 1")
        params.append(page_id)
        cur.execute(f"UPDATE lowcode_page SET {', '.join(fields)} WHERE id = %s", params)
    return get_page(page_id, user)


def delete_page(page_id: str, user: dict) -> None:
    """删页面（连带删它的全部发布快照）；仅负责人或管理员可删。"""
    with db.db_session() as cur:
        row = _fetch_row(cur, page_id)
        if row is None:
            raise NotFound()
        # 删除比普通写更严：只认 admin 或 owner（viewer/editor 都不行）
        if not (user["role"] == "admin" or row["owner_id"] == user["id"]):
            raise Forbidden("只有负责人或管理员可以删除页面")
        # 先删快照再删主表：没有外键约束，手动保顺序防孤儿记录
        cur.execute("DELETE FROM lowcode_page_version WHERE page_id = %s", (page_id,))
        cur.execute("DELETE FROM lowcode_page WHERE id = %s", (page_id,))


# ---------------- 发布 / 版本 ----------------
# 模型：当前草稿在 lowcode_page；每次 publish 把当时的 schema_json 拍一张快照
# 写进 lowcode_page_version。回滚不是「覆盖历史」，而是把旧快照内容搬回草稿并
# version+1，所以历史快照永远只增不改，可追溯。

def publish_page(page_id: str, user: dict, comment: str = "") -> dict:
    """把当前草稿拍一张发布快照，并把页面状态置为 published。

    快照主键是 (page_id, version)：同一 version 重复 publish 用 ON DUPLICATE KEY
    覆盖更新（而不是插出第二行），相当于「重新发布当前版」。
    """
    with db.db_session() as cur:
        row = _fetch_row(cur, page_id)
        if row is None:
            raise NotFound()
        if not _can_write(user, row):
            raise Forbidden("没有权限发布该页面")
        # 以当前 version 为快照号存当前 schema_json；冲突时刷新内容/备注/时间
        cur.execute(
            "INSERT INTO lowcode_page_version (page_id, version, schema_json, comment) "
            "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE schema_json = VALUES(schema_json), "
            "comment = VALUES(comment), created_at = CURRENT_TIMESTAMP",
            (page_id, int(row["version"]), row["schema_json"], comment),
        )
        cur.execute("UPDATE lowcode_page SET status = 'published' WHERE id = %s", (page_id,))
    return get_page(page_id, user)


def list_versions(page_id: str, user: dict) -> list[dict]:
    """列出某页面的全部发布快照（按版本号倒序，最新在前）。"""
    get_page(page_id, user)  # 复用读权限校验
    with db.db_session() as cur:
        cur.execute(
            "SELECT version, comment, created_at FROM lowcode_page_version "
            "WHERE page_id = %s ORDER BY version DESC",
            (page_id,),
        )
        return [
            {"version": int(r["version"]), "comment": r["comment"], "created_at": _dt(r["created_at"])}
            for r in cur.fetchall()
        ]


def get_version(page_id: str, version: int, user: dict) -> dict:
    """某个发布快照的完整内容（版本对比预览用）。"""
    get_page(page_id, user)  # 复用读权限校验
    with db.db_session() as cur:
        cur.execute(
            "SELECT version, schema_json, comment, created_at FROM lowcode_page_version "
            "WHERE page_id = %s AND version = %s",
            (page_id, version),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFound(f"版本 {version} 不存在")
    try:
        schema = json.loads(row["schema_json"])
    except (TypeError, ValueError) as exc:
        raise LowcodeError("该版本的快照已损坏") from exc
    return {
        "version": int(row["version"]),
        "comment": row["comment"],
        "created_at": _dt(row["created_at"]),
        "schema": schema,
    }


def rollback_page(page_id: str, user: dict, version: int) -> dict:
    """回滚：把指定历史快照的 schema 搬回当前草稿，并 version+1、置回 draft。

    注意是「回滚成一份新草稿」而非覆盖历史——旧快照仍保留，可再回滚到别处。
    """
    with db.db_session() as cur:
        row = _fetch_row(cur, page_id)
        if row is None:
            raise NotFound()
        if not _can_write(user, row):
            raise Forbidden("没有权限回滚该页面")
        cur.execute(
            "SELECT schema_json FROM lowcode_page_version WHERE page_id = %s AND version = %s",
            (page_id, version),
        )
        snapshot = cur.fetchone()
        if snapshot is None:
            raise NotFound(f"版本 {version} 不存在")
        # 把旧快照内容写回当前草稿；版本自增、状态退回 draft（需再次发布才上线）
        cur.execute(
            "UPDATE lowcode_page SET schema_json = %s, version = version + 1, status = 'draft' "
            "WHERE id = %s",
            (snapshot["schema_json"], page_id),
        )
    return get_page(page_id, user)
