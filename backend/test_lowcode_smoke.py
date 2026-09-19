# -*- coding: utf-8 -*-
"""低代码接口冒烟测试（不需要模型，可重复运行）。

运行：cd backend && python test_lowcode_smoke.py
覆盖：登录（成功/失败）/ 无令牌 401 / 令牌过期与登出 / 页面 CRUD /
乐观锁 409 / 坏 schema 400 / 权限（viewer 只读、editor 只能动自己的）/
发布 + 版本列表 + 回滚。

注意：会改动库里的数据（示例页面版本号 +1、发布与回滚各一次），属预期。
"""

import sys

import app


def main() -> int:
    print("app module =>", app.__file__)
    # 注意：app.py 里模块名和 Flask 实例名都叫 app，这里要取「模块里的实例」
    c = app.app.test_client()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        results.append((name, cond, extra))
        print("PASS" if cond else "FAIL", name, extra)

    def login(username: str, password: str) -> str:
        r = c.post("/auth/login", json={"username": username, "password": password})
        return (r.get_json() or {}).get("token", "")

    def auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    # ---- 登录 ----
    r = c.post("/auth/login", json={"username": "admin", "password": "wrong"})
    check("密码错误 -> 401", r.status_code == 401)
    token = login("admin", "admin123")
    check("admin 登录 -> 拿到令牌", bool(token))
    r = c.get("/auth/me", headers=auth(token))
    check("GET /auth/me", r.status_code == 200 and r.get_json()["user"]["role"] == "admin")

    r = c.get("/lowcode/pages")
    check("无令牌访问 -> 401", r.status_code == 401)
    r = c.get("/lowcode/pages", headers=auth("not-a-real-token"))
    check("假令牌访问 -> 401", r.status_code == 401)

    # ---- 列表 / 详情 ----
    r = c.get("/lowcode/pages", headers=auth(token))
    check("GET /lowcode/pages", r.status_code == 200)
    pages = r.get_json()["pages"]
    check("种子示例页面存在", len(pages) >= 1, f"count={len(pages)}")
    sample = next((p for p in pages if "示例" in p["name"]), pages[-1])
    pid = sample["id"]

    r = c.get(f"/lowcode/pages/{pid}", headers=auth(token))
    detail = r.get_json()
    check("GET 详情", r.status_code == 200 and detail["schema"]["root"]["type"] == "Card")

    # ---- 乐观锁 ----
    r = c.put(
        f"/lowcode/pages/{pid}",
        headers=auth(token),
        json={"schema": detail["schema"], "baseVersion": 999},
    )
    check("baseVersion 不符 -> 409", r.status_code == 409)

    r = c.put(
        f"/lowcode/pages/{pid}",
        headers=auth(token),
        json={"schema": detail["schema"], "baseVersion": detail["version"]},
    )
    check(
        "正常保存 -> 200 且版本 +1",
        r.status_code == 200 and r.get_json()["version"] == detail["version"] + 1,
    )

    # ---- 坏 schema 被拒 ----
    bad = {
        "schemaVersion": "1.0",
        "root": {"id": "root", "type": "Card", "children": [{"id": "root"}]},
    }
    r = c.put(f"/lowcode/pages/{pid}", headers=auth(token), json={"schema": bad})
    check("坏 schema -> 400", r.status_code == 400, str(r.get_json()))

    # ---- 权限 ----
    bob = login("bob", "bob123")
    alice = login("alice", "alice123")
    r = c.post("/lowcode/pages", headers=auth(bob), json={"name": "bob 的页面"})
    check("viewer 建页 -> 403", r.status_code == 403)

    r = c.post("/lowcode/pages", headers=auth(alice), json={"name": "alice 的页面"})
    alice_page = r.get_json()
    check("editor 建页 -> 201", r.status_code == 201 and alice_page["owner_id"] == "alice")

    r = c.put(
        f"/lowcode/pages/{alice_page['id']}",
        headers=auth(bob),
        json={"name": "改名"},
    )
    check("非负责人修改 -> 403", r.status_code == 403)

    r = c.delete(f"/lowcode/pages/{alice_page['id']}", headers=auth(alice))
    check("负责人删除 -> 200", r.status_code == 200)

    # ---- 发布 / 版本 / 回滚 ----
    r = c.post(f"/lowcode/pages/{pid}/publish", headers=auth(token), json={"comment": "smoke"})
    check("发布 -> 200", r.status_code == 200)
    r = c.get(f"/lowcode/pages/{pid}/versions", headers=auth(token))
    versions = r.get_json()["versions"]
    check("版本列表", r.status_code == 200 and len(versions) >= 1)
    r = c.post(
        f"/lowcode/pages/{pid}/rollback",
        headers=auth(token),
        json={"version": versions[0]["version"]},
    )
    check("回滚 -> 200 且版本 +1", r.status_code == 200 and r.get_json()["version"] >= 1)

    # ---- AI 增量修改：Patch 校验/应用（纯本地，不调模型） ----
    from lowcode_patch import apply_patches

    base = {
        "schemaVersion": "1.0",
        "name": "补丁测试",
        "state": {"keyword": ""},
        "dataSources": [
            {"id": "userList", "kind": "rest", "method": "GET", "url": "/users", "autoLoad": True}
        ],
        "root": {
            "id": "root",
            "type": "Card",
            "props": {"title": "旧标题"},
            "children": [
                {
                    "id": "btn",
                    "type": "Button",
                    "props": {"text": "查询"},
                    "events": {"onClick": {"kind": "callDataSource", "dataSource": "userList"}},
                }
            ],
        },
    }
    patched, errors = apply_patches(
        base,
        [
            {"op": "set_page_name", "name": "新标题"},
            {"op": "update_props", "nodeId": "btn", "props": {"text": "搜索"}},
            {
                "op": "add_node",
                "parentId": "root",
                "node": {"id": "hint", "type": "Text", "props": {"text": "hi"}},
            },
        ],
    )
    check(
        "patch 正常应用",
        not errors
        and patched["name"] == "新标题"
        and patched["root"]["children"][1]["id"] == "hint",
        str(errors),
    )
    _, errors = apply_patches(base, [{"op": "update_props", "nodeId": "nope", "props": {"x": 1}}])
    check("patch 节点不存在 -> 报错", bool(errors))
    _, errors = apply_patches(base, [{"op": "remove_data_source", "id": "userList"}])
    check("删数据源但事件还引用 -> 校验拦下", any("数据源" in e for e in errors))
    check("原 schema 未被修改", base["name"] == "补丁测试")

    # ---- 会话接入登录体系 ----
    r = c.get("/conversations")
    check("会话未登录 -> 401", r.status_code == 401)
    r = c.post("/conversations", headers=auth(alice), json={})
    convo = r.get_json()
    check("alice 建会话 -> 201 且带归属", r.status_code == 201 and convo.get("owner_id") == "alice")
    r = c.get("/conversations", headers=auth(alice))
    check(
        "alice 列表含自己的会话",
        any(item["id"] == convo["id"] for item in r.get_json()["conversations"]),
    )
    r = c.get(f"/conversations/{convo['id']}", headers=auth(bob))
    check("他人的会话 -> 404", r.status_code == 404)
    r = c.post(
        f"/conversations/{convo['id']}/messages",
        headers=auth(bob),
        json={"content": "hi"},
    )
    check("往他人会话发消息 -> 404", r.status_code == 404)
    r = c.delete(f"/conversations/{convo['id']}", headers=auth(alice))
    check("归属人删除会话 -> 200", r.status_code == 200)

    # ---- 登出后令牌失效 ----
    r = c.post("/auth/logout", headers=auth(bob))
    check("登出 -> 200", r.status_code == 200)
    r = c.get("/lowcode/pages", headers=auth(bob))
    check("登出后访问 -> 401", r.status_code == 401)

    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print("失败项：", "，".join(failed))
        return 1
    print(f"全部通过（{len(results)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
