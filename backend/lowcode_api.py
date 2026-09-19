# -*- coding: utf-8 -*-
"""低代码平台 REST 接口（Blueprint，风格与 conversation_api.py 一致）。

路径不带 /api 前缀：前端 Vite 代理会把 /api 去掉（见 vite.config.ts）。
鉴权：统一 `Authorization: Bearer <token>`（登录/令牌逻辑在 lowcode_auth.py）。
"""

from flask import Blueprint, jsonify, request

import lowcode_store as store
from lowcode_auth import require_user

lowcode_bp = Blueprint("lowcode", __name__)


@lowcode_bp.errorhandler(store.LowcodeError)
def _on_lowcode_error(exc: store.LowcodeError):
    return jsonify({"error": str(exc)}), exc.code


@lowcode_bp.get("/lowcode/pages")
def api_list_pages():
    return jsonify({"pages": store.list_pages(require_user())})


@lowcode_bp.post("/lowcode/pages")
def api_create_page():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise store.LowcodeError("name 必填")
    page = store.create_page(
        require_user(),
        name,
        (data.get("description") or "").strip(),
        data.get("visibility") or "private",
    )
    return jsonify(page), 201


@lowcode_bp.get("/lowcode/pages/<page_id>")
def api_get_page(page_id: str):
    return jsonify(store.get_page(page_id, require_user()))


@lowcode_bp.put("/lowcode/pages/<page_id>")
def api_update_page(page_id: str):
    data = request.get_json(silent=True) or {}
    base_version = data.get("baseVersion")
    if base_version is not None:
        try:
            base_version = int(base_version)
        except (TypeError, ValueError):
            raise store.LowcodeError("baseVersion 必须是整数") from None
    page = store.update_page(
        page_id,
        require_user(),
        schema=data.get("schema"),
        name=data.get("name"),
        visibility=data.get("visibility"),
        base_version=base_version,
    )
    return jsonify(page)


@lowcode_bp.delete("/lowcode/pages/<page_id>")
def api_delete_page(page_id: str):
    store.delete_page(page_id, require_user())
    return jsonify({"ok": True})


@lowcode_bp.post("/lowcode/pages/<page_id>/publish")
def api_publish_page(page_id: str):
    data = request.get_json(silent=True) or {}
    return jsonify(store.publish_page(page_id, require_user(), (data.get("comment") or "").strip()))


@lowcode_bp.get("/lowcode/pages/<page_id>/versions")
def api_list_versions(page_id: str):
    return jsonify({"versions": store.list_versions(page_id, require_user())})


@lowcode_bp.get("/lowcode/pages/<page_id>/versions/<int:version>")
def api_get_version(page_id: str, version: int):
    """单个发布快照的完整 schema（版本对比预览用）。"""
    return jsonify(store.get_version(page_id, version, require_user()))


@lowcode_bp.post("/lowcode/pages/<page_id>/rollback")
def api_rollback_page(page_id: str):
    data = request.get_json(silent=True) or {}
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise store.LowcodeError("version 必须是整数")
    return jsonify(store.rollback_page(page_id, require_user(), version))
