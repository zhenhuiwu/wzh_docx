"""A05 验收修复（19 审查 / 20 测试 / 21 界面验收，2026-10-11 一轮修完）的后端用例。

每条都在 3a1f5f3（修复前）上失败；fixture 复用 test_project_members_a05。
"""

from __future__ import annotations

from uuid import uuid4

from app.db import connect
from app.files import store_bytes
from app.identity import create_user
from tests.test_project_members_a05 import PASSWORD, _archive, _code, _login, ctx  # noqa: F401


def _unarchive(ctx):
    ctx.as_("admin")
    restored = ctx.req("POST", f"/projects/{ctx.project}/unarchive", {"expectedRevision": ctx.project_rev(), "reason": "恢复"})
    assert restored.status_code == 200, restored.text


# ---------- C-1：归档项目的项目范围知识库也只读（测试 L-1 / 审查 S-6） ----------

def _upload(ctx, base_id, name):
    return ctx.client.post("/api/v1/files", headers={**ctx.headers, "Idempotency-Key": str(uuid4())},
                           data={"purpose": "source", "baseId": base_id},
                           files={"file": (name, b"knowledge text", "text/plain")})


def test_archived_project_knowledge_is_read_only(ctx):
    with connect() as connection:
        keeper = create_user(account="a05-keeper", name="知识维护", password=PASSWORD, roles=["knowledge_manager"],
                             connection=connection)
        connection.commit()
    ctx.as_("admin")
    base = ctx.req("POST", "/knowledge-bases", {"name": "项目库", "scope": "project", "projectId": ctx.project,
                                                 "ownerId": str(keeper)})
    assert base.status_code == 201, base.text
    base = base.json()["data"]
    org = ctx.req("POST", "/knowledge-bases", {"name": "单位库", "scope": "organization", "ownerId": str(keeper)})
    assert org.status_code == 201, org.text
    first = store_bytes(name="a.txt", media_type="text/plain", purpose="source", content=b"a", base_id=base["id"])
    source = ctx.req("POST", f"/knowledge-bases/{base['id']}/sources", {"name": "资料一", "fileId": str(first.id)})
    assert source.status_code == 201, source.text
    source = source.json()["data"]
    _archive(ctx)
    second = store_bytes(name="b.txt", media_type="text/plain", purpose="source", content=b"b", base_id=base["id"])
    for who in ("admin", "keeper"):
        if who == "keeper":
            ctx.headers = _login(ctx.client, "a05-keeper")
        else:
            ctx.as_("admin")
        current = ctx.get(f"/knowledge-bases/{base['id']}").json()["data"]
        blocked = {
            "create base": ctx.req("POST", "/knowledge-bases", {"name": "新库", "scope": "project", "projectId": ctx.project,
                                                                "ownerId": str(keeper)}),
            "create source": ctx.req("POST", f"/knowledge-bases/{base['id']}/sources", {"name": "资料二", "fileId": str(second.id)}),
            "new version": ctx.req("POST", f"/sources/{source['sourceId']}/versions", {"fileId": str(second.id)}),
            "patch base": ctx.req("PATCH", f"/knowledge-bases/{base['id']}", {"expectedRevision": current["revision"], "name": "改名"}),
            "access": ctx.req("PUT", f"/knowledge-bases/{base['id']}/access",
                              {"expectedRevision": current["revision"], "readers": [], "maintainers": []}),
            "archive base": ctx.req("POST", f"/knowledge-bases/{base['id']}/archive", {"expectedRevision": current["revision"], "reason": "x"}),
            "upload": _upload(ctx, base["id"], "c.txt"),
        }
        for name, response in blocked.items():
            assert _code(response) == (409, "PROJECT_ARCHIVED"), (who, name, response.text)
            assert response.json()["error"]["details"]["readonlyReason"] == "archived"
        # 读照常；单位知识库不受影响
        assert ctx.get(f"/knowledge-bases/{base['id']}/sources").status_code == 200
        assert ctx.req("PATCH", f"/knowledge-bases/{org.json()['data']['id']}",
                       {"expectedRevision": ctx.get(f"/knowledge-bases/{org.json()['data']['id']}").json()["data"]["revision"],
                        "summary": f"{who} 改"}).status_code == 200
    with connect() as connection:
        assert connection.execute("select count(*) from knowledge_bases where project_id = %s", (ctx.project,)).fetchone()[0] == 1
        assert connection.execute("select count(*) from sources where base_id = %s", (base["id"],)).fetchone()[0] == 1
    # 看不到的人仍是 404（可见性先于归档）
    ctx.as_("outsider")
    assert ctx.req("POST", f"/knowledge-bases/{base['id']}/sources", {"name": "x", "fileId": str(second.id)}).status_code in (403, 404)
    _unarchive(ctx)
    again = ctx.req("POST", f"/knowledge-bases/{base['id']}/sources", {"name": "资料二", "fileId": str(second.id)})
    assert again.status_code == 201, again.text


# ---------- 测试 I-3（2026-10-11 决定）：成员 PUT 只改已有成员的角色，不再 upsert ----------

def test_put_does_not_re_add_a_removed_member(ctx):
    assert ctx.batch(["bob"], "editor").status_code == 200
    assert ctx.delete("bob").status_code == 200  # 别人刚把李工移出
    revision, events = ctx.rev(), len(ctx.events())
    # 盲目重试：带上最新的版本号再 PUT，旧实现会把李工重新加回（200 + add 审计）
    retried = ctx.put("bob", "viewer", revision=revision)
    assert _code(retried) == (404, "MEMBER_NOT_FOUND"), retried.text
    assert retried.json()["error"]["details"]["userId"] == str(ctx.users["bob"])
    assert ctx.rev() == revision and len(ctx.events()) == events
    assert "李工" not in {item["name"] for item in ctx.members()["items"]}
    # 从没加入过的账号、停用账号同样 404；加入只走批量接口
    assert _code(ctx.put("carol", "owner")) == (404, "MEMBER_NOT_FOUND")
    assert ctx.disable("dave").status_code == 200
    ctx.as_("alice")
    assert _code(ctx.put("dave", "viewer")) == (404, "MEMBER_NOT_FOUND")
    # 权限、版本号检查仍在前：编写人员 403，过期版本 409
    assert _code(ctx.put("carol", "owner", revision=revision - 1)) == (409, "REVISION_CONFLICT")
    assert ctx.batch(["carol"], "viewer").status_code == 200
    changed = ctx.put("carol", "editor")
    assert changed.status_code == 200 and changed.json()["data"]["item"]["role"] == "editor"
    ctx.as_("carol")
    assert _code(ctx.put("bob", "viewer")) == (403, "FORBIDDEN")
