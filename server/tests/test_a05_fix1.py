"""A05 验收修复（19 审查 / 20 测试 / 21 界面验收，2026-10-11 一轮修完）的后端用例。

每条都在 3a1f5f3（修复前）上失败；fixture 复用 test_project_members_a05。
"""

from __future__ import annotations

from uuid import uuid4

from app.db import connect
from app.files import store_bytes
from app.identity import create_user
from tests.test_project_members_a05 import PASSWORD, _archive, _code, _login, _user_revision, ctx  # noqa: F401


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


# ---------- 审查 S-1~S-4：读时计算有效主负责人；停用不改库，也不由无关成员操作“懒改指” ----------

def _stored_owner(ctx):
    with connect() as connection:
        return str(connection.execute("select owner_id from projects where id = %s", (ctx.project,)).fetchone()[0])


def _reassign_events(ctx):
    with connect() as connection:
        return connection.execute(
            "select actor_id, target_user_id from project_member_events where project_id = %s and action = 'owner_reassigned' order by rowid",
            (ctx.project,)).fetchall()


def _owner_views(ctx):
    """各个读接口给出的（主负责人 id, 姓名, owners 姓名）。"""
    detail = ctx.get(f"/projects/{ctx.project}").json()["data"]
    listed = next(item for item in ctx.get("/projects?pageSize=50").json()["data"]["items"] if item["id"] == ctx.project)
    members = ctx.members()
    bench = next((item for item in ctx.get("/workbench").json()["data"]["recentProjects"] if item["id"] == ctx.project), None)
    return {
        "detail": (detail["ownerId"], detail["ownerName"], [o["name"] for o in detail["owners"]]),
        "list": (listed["ownerId"], listed["ownerName"], [o["name"] for o in listed["owners"]]),
        "members": (members["ownerId"], None, [o["name"] for o in members["owners"]]),
        "workbench": None if bench is None else (bench["ownerId"], bench["ownerName"], None),
        "primary_flags": [o["isPrimary"] for o in detail["owners"]],
        "statuses": {o["status"] for o in detail["owners"] + listed["owners"] + members["owners"]},
    }


def test_effective_owner_is_computed_at_read_time(ctx):
    from app.document_agent import _project_overview

    assert ctx.batch(["bob"], "owner").status_code == 200
    assert ctx.batch(["carol"], "editor").status_code == 200
    alice, bob = str(ctx.users["alice"]), str(ctx.users["bob"])
    assert ctx.disable("alice").status_code == 200  # admin；李工仍是启用负责人
    views = _owner_views(ctx)
    assert views["detail"] == (bob, "李工", ["李工"]) and views["list"] == (bob, "李工", ["李工"])
    assert views["members"] == (bob, None, ["李工"]) and views["workbench"] == (bob, "李工", None)
    assert views["primary_flags"] == [True] and views["statuses"] == {"active"}  # owners[] 只含启用账号
    assert ctx.get(f"/projects?ownerId={bob}").json()["data"]["total"] == 1
    assert ctx.get(f"/projects?ownerId={alice}").json()["data"]["total"] == 0
    overview = _project_overview(ctx.project)
    assert "负责人：李工" in overview and "张工" not in overview
    # 读不改库
    assert _stored_owner(ctx) == alice and _reassign_events(ctx) == []

    # 无关的成员操作（改丁/王的角色、加人）不触发改指、不提示、不记在操作者名下
    changed = ctx.put("carol", "viewer")
    assert changed.status_code == 200 and changed.json()["data"]["ownerChanged"] is None
    added = ctx.batch(["dave"], "viewer")
    assert added.status_code == 200 and added.json()["data"]["ownerChanged"] is None
    assert _stored_owner(ctx) == alice and _reassign_events(ctx) == []

    # 重新启用：自动恢复为主负责人
    assert ctx.req("POST", f"/users/{alice}/enable", {"expectedRevision": _user_revision(alice), "reason": "恢复"}).status_code == 200
    views = _owner_views(ctx)
    assert views["detail"] == (alice, "张工", ["张工", "李工"]) and views["primary_flags"] == [True, False]
    assert ctx.get(f"/projects?ownerId={alice}").json()["data"]["total"] == 1


def test_demoting_the_disabled_primary_aligns_storage_without_a_notice(ctx):
    """操作对象就是（已停用的）主负责人：存储值对齐到有效主负责人，审计记在操作者名下；界面上主负责人本来就显示李工，不再提示。"""
    assert ctx.batch(["bob"], "owner").status_code == 200
    assert ctx.disable("alice").status_code == 200
    demoted = ctx.put("alice", "viewer")  # 停用账号可以被降级
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["data"]["ownerChanged"] is None and demoted.json()["data"]["ownerId"] == str(ctx.users["bob"])
    assert _stored_owner(ctx) == str(ctx.users["bob"])
    assert [(str(row["actor_id"]), str(row["target_user_id"])) for row in _reassign_events(ctx)] == [(str(ctx.users["admin"]), str(ctx.users["bob"]))]
    # 降级启用中的主负责人：照常提示
    ctx.as_("bob")
    assert ctx.batch(["carol"], "owner").status_code == 200
    moved = ctx.put("bob", "editor")
    assert moved.json()["data"]["ownerChanged"]["to"]["name"] == "王工"


def test_project_without_active_owner_reports_none(ctx):
    """历史数据：唯一负责人已停用（A05 之前可能出现）。ownerId/ownerName 为 null、owners 为空，界面据此显示警告。"""
    with connect() as connection:
        connection.execute("update users set status = 'disabled' where id = %s", (ctx.users["alice"],))
        connection.commit()
    from app.document_agent import _project_overview

    ctx.as_("admin")
    views = _owner_views(ctx)
    assert views["detail"] == (None, None, []) and views["list"] == (None, None, []) and views["members"] == (None, None, [])
    assert views["workbench"] == (None, None, None)
    assert ctx.members()["activeOwnerCount"] == 0
    assert "负责人" not in _project_overview(ctx.project)
    # 改项目资料不会把主负责人“顺手”改掉，也不会因为 ownerId 为空出错
    patched = ctx.req("PATCH", f"/projects/{ctx.project}", {"expectedRevision": ctx.project_rev(), "description": "改简介"})
    assert patched.status_code == 200, patched.text
    assert _stored_owner(ctx) == str(ctx.users["alice"])
    # 补一位负责人后恢复显示
    assert ctx.batch(["bob"], "owner").status_code == 200
    assert _owner_views(ctx)["detail"] == (str(ctx.users["bob"]), "李工", ["李工"])


# ---------- 21 M-1：恢复项目入口所需的 canArchive（与 archive/unarchive 接口同一判定） ----------

def test_can_archive_matches_the_archive_endpoints(ctx):
    with connect() as connection:
        plain_owner = create_user(account="a05-plainowner", name="无全局负责人", password=PASSWORD, roles=[],
                                  allow_no_roles=True, connection=connection)
        connection.commit()
    ctx.users["plainowner"] = plain_owner
    assert ctx.batch(["bob"], "owner").status_code == 200  # 李工：project_creator（有 projects.manage）
    assert ctx.batch(["plainowner"], "owner").status_code == 200  # 没有全局角色的负责人
    assert ctx.batch(["carol"], "editor").status_code == 200
    _archive(ctx)
    expected = {"admin": True, "bob": True, "plainowner": False, "carol": False, "auditor": False}
    for who, allowed in expected.items():
        ctx.headers = _login(ctx.client, f"a05-{who}")
        detail = ctx.get(f"/projects/{ctx.project}").json()["data"]
        assert detail["permissions"]["canArchive"] is allowed, who
        assert detail["permissions"]["canEdit"] is False and detail["archivedAt"]
        response = ctx.req("POST", f"/projects/{ctx.project}/unarchive", {"expectedRevision": detail["revision"], "reason": "核对"})
        assert (response.status_code == 200) is allowed, (who, response.text)
        if allowed:  # 再归档回去，供下一位核对
            again = ctx.req("POST", f"/projects/{ctx.project}/archive", {"expectedRevision": response.json()["data"]["revision"], "reason": "核对"})
            assert again.status_code == 200, again.text
