"""User administration stays on the server."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user, verify_password

PASSWORD = "correct-horse-1"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_ACCOUNT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    from app.config import clear_settings_cache

    clear_settings_cache()
    migrate()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _reset(account: str) -> None:
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                """
                delete from user_admin_events
                where user_id in (select id from users where account_key = %s)
                   or actor_id in (select id from users where account_key = %s)
                """,
                (account, account),
            )
            connection.execute(
                """
                delete from idempotency_keys
                where user_id in (select id from users where account_key = %s)
                   or result_user_id in (select id from users where account_key = %s)
                """,
                (account, account),
            )
            connection.execute(
                """
                delete from sessions
                where user_id in (select id from users where account_key = %s)
                """,
                (account,),
            )
            connection.execute(
                """
                delete from user_roles
                where user_id in (select id from users where account_key = %s)
                """,
                (account,),
            )
            connection.execute("delete from users where account_key = %s", (account,))


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["data"]["csrfToken"]


def _login(client: TestClient, account: str, password: str = PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": password},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def _admin(client: TestClient) -> str:
    _reset("dev04-admin")
    create_user(
        account="dev04-admin",
        name="管理员",
        password=PASSWORD,
        roles=["system_admin"],
    )
    return _login(client, "dev04-admin")


def test_auditor_cannot_read_or_change_users(client: TestClient) -> None:
    _reset("dev04-auditor")
    create_user(
        account="dev04-auditor",
        name="审计",
        password=PASSWORD,
        roles=["auditor"],
    )
    _login(client, "dev04-auditor")
    listed = client.get("/api/v1/users")
    assert listed.status_code == 403
    assert "password" not in listed.text.lower()
    created = client.post(
        "/api/v1/users",
        json={
            "account": "dev04-made",
            "name": "新人",
            "globalRoles": ["auditor"],
            "password": PASSWORD,
        },
        headers={"Idempotency-Key": "dev04-made", "X-CSRF-Token": "nope"},
    )
    assert created.status_code == 403


def test_admin_creates_without_returning_password(client: TestClient) -> None:
    _reset("dev04-new")
    _reset("dev04-other")
    csrf = _admin(client)
    created = client.post(
        "/api/v1/users",
        json={
            "account": "dev04-new",
            "name": "新同事",
            "department": "总体",
            "globalRoles": ["auditor"],
            "password": PASSWORD,
        },
        headers={"Idempotency-Key": "dev04-new", "X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    body = created.json()["data"]
    assert body["account"] == "dev04-new"
    assert "password" not in created.text.lower()
    assert "passwordHash" not in body
    with connect() as connection:
        row = connection.execute(
            "select password_hash from users where account_key = 'dev04-new'"
        ).fetchone()
    assert row is not None
    assert verify_password(PASSWORD, row["password_hash"])
    again = client.post(
        "/api/v1/users",
        json={
            "account": "dev04-new",
            "name": "新同事",
            "department": "总体",
            "globalRoles": ["auditor"],
            "password": PASSWORD,
        },
        headers={"Idempotency-Key": "dev04-new", "X-CSRF-Token": csrf},
    )
    assert again.status_code == 201
    assert again.json()["data"]["id"] == body["id"]
    conflict = client.post(
        "/api/v1/users",
        json={
            "account": "dev04-other",
            "name": "另一个",
            "globalRoles": ["auditor"],
            "password": PASSWORD,
        },
        headers={"Idempotency-Key": "dev04-new", "X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409


def test_last_admin_and_disable_revoke_session(client: TestClient) -> None:
    csrf = _admin(client)
    _reset("dev04-member")
    create_user(
        account="dev04-member",
        name="成员",
        password=PASSWORD,
        roles=["project_creator"],
    )
    member = TestClient(client.app)
    member_csrf = _login(member, "dev04-member")
    assert member.get("/api/v1/auth/me").status_code == 200
    listed = client.get("/api/v1/users", params={"account": "dev04-member"})
    assert listed.status_code == 200
    payload = listed.json()["data"]
    assert payload["total"] == 1
    assert "password" not in listed.text.lower()
    user = payload["items"][0]
    denied = client.post(
        f"/api/v1/users/{user['id']}/disable",
        json={"expectedRevision": 0, "reason": "停用成员"},
        headers={"X-CSRF-Token": "bad"},
    )
    assert denied.status_code == 403
    disabled = client.post(
        f"/api/v1/users/{user['id']}/disable",
        json={"expectedRevision": user["revision"], "reason": "停用成员"},
        headers={"X-CSRF-Token": csrf},
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"
    assert member.get("/api/v1/auth/me").status_code == 401
    del member_csrf
    with connect() as connection:
        with connection.transaction():
            others = connection.execute(
                """
                select u.id
                from users u
                join user_roles r on r.user_id = u.id
                where r.role = 'system_admin'
                  and u.status = 'active'
                  and u.account_key <> 'dev04-admin'
                """
            ).fetchall()
            for row in others:
                connection.execute(
                    "update users set status = 'disabled' where id = %s",
                    (row["id"],),
                )
    try:
        admins = client.get(
            "/api/v1/users",
            params={"account": "dev04-admin", "status": "active"},
        )
        admin = admins.json()["data"]["items"][0]
        keep = client.post(
            f"/api/v1/users/{admin['id']}/disable",
            json={"expectedRevision": admin["revision"], "reason": "停掉最后的管理员"},
            headers={"X-CSRF-Token": csrf},
        )
        assert keep.status_code == 409
        assert keep.json()["error"]["code"] == "INVALID_TRANSITION"
    finally:
        with connect() as connection:
            with connection.transaction():
                for row in others:
                    connection.execute(
                        "update users set status = 'active' where id = %s",
                        (row["id"],),
                    )
    jobs = None
    with connect() as connection:
        jobs = connection.execute("select name from sqlite_master where type = 'table' and name = 'jobs'").fetchone()
    assert jobs is not None
    assert jobs["name"] == "jobs"


# ---- 新建用户的全局角色（09 视觉分支审查《新建用户默认角色的后端建议》） ----


def _create(client: TestClient, csrf: str, account: str, key: str, roles=None):
    body = {"account": account, "name": "角色用例", "password": PASSWORD}
    if roles is not None:
        body["globalRoles"] = roles
    return client.post(
        "/api/v1/users",
        json=body,
        headers={"Idempotency-Key": key, "X-CSRF-Token": csrf},
    )


def _events(account: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            select e.action, e.reason
            from user_admin_events e join users u on u.id = e.user_id
            where u.account_key = %s
            order by e.created_at, e.action
            """,
            (account,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_create_without_roles_makes_project_member_only(client: TestClient) -> None:
    _reset("dev04-norole")
    csrf = _admin(client)
    created = _create(client, csrf, "dev04-norole", "dev04-norole")
    assert created.status_code == 201, created.text
    assert created.json()["data"]["globalRoles"] == []
    listed = client.get("/api/v1/users", params={"account": "dev04-norole"})
    assert listed.json()["data"]["items"][0]["globalRoles"] == []
    member = TestClient(client.app)
    _login(member, "dev04-norole")
    me = member.get("/api/v1/auth/me").json()["data"]
    assert me["permissions"] == []
    # 只看到自己是成员的项目：本分支 GET /projects 仍要求 projects.read（403）；
    # phase2 S1 开放给成员后应返回 200 且只有自己加入的项目（这里一个都没有）。
    projects = member.get("/api/v1/projects")
    assert projects.status_code in {200, 403}
    if projects.status_code == 200:
        assert projects.json()["data"]["total"] == 0


def test_create_rejects_unknown_role(client: TestClient) -> None:
    _reset("dev04-badrole")
    csrf = _admin(client)
    created = _create(client, csrf, "dev04-badrole", "dev04-badrole", ["no_such"])
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "VALIDATION_FAILED"


def test_create_rejects_more_than_ten_roles(client: TestClient) -> None:
    _reset("dev04-manyroles")
    csrf = _admin(client)
    created = _create(client, csrf, "dev04-manyroles", "dev04-manyroles", ["auditor"] * 11)
    assert created.status_code == 422


def test_only_system_admin_can_grant_system_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import permissions

    # 一个拿到全部管理员权限、但本身不是 system_admin 的测试角色。
    monkeypatch.setitem(
        permissions._ROLE_PERMISSIONS,
        "test_user_admin",
        permissions._ROLE_PERMISSIONS["system_admin"],
    )
    _reset("dev04-useradmin")
    _reset("dev04-granted")
    create_user(account="dev04-useradmin", name="账号管理员", password=PASSWORD, roles=["auditor"])
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                "select id from users where account_key = 'dev04-useradmin'"
            ).fetchone()
            connection.execute("delete from user_roles where user_id = %s", (row["id"],))
            connection.execute(
                "insert into user_roles (user_id, role) values (%s, 'test_user_admin')",
                (row["id"],),
            )
    csrf = _login(client, "dev04-useradmin")
    denied = _create(client, csrf, "dev04-granted", "dev04-granted", ["system_admin"])
    assert denied.status_code == 403
    assert "系统管理员" in denied.json()["error"]["message"]
    allowed = _create(client, csrf, "dev04-granted", "dev04-granted-2", ["auditor"])
    assert allowed.status_code == 201, allowed.text
    _reset("dev04-useradmin")



def test_cannot_grant_role_beyond_own_permissions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """通用路径：只有账号管理权限的角色，不能授予比自己权限多的角色（新建与改角色两条入口）。"""
    from app import permissions

    monkeypatch.setitem(
        permissions._ROLE_PERMISSIONS,
        "test_users_only",
        frozenset({"users.read", "users.manage"}),
    )
    _reset("dev04-usersonly")
    _reset("dev04-beyond")
    _reset("dev04-beyond-ok")
    create_user(account="dev04-usersonly", name="仅账号管理", password=PASSWORD, roles=["auditor"])
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                "select id from users where account_key = 'dev04-usersonly'"
            ).fetchone()
            connection.execute("delete from user_roles where user_id = %s", (row["id"],))
            connection.execute(
                "insert into user_roles (user_id, role) values (%s, 'test_users_only')",
                (row["id"],),
            )
    csrf = _login(client, "dev04-usersonly")

    denied = _create(client, csrf, "dev04-beyond", "dev04-beyond", ["project_creator"])
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["message"] == "不能分配超出自己权限的角色。"
    assert client.get("/api/v1/users", params={"account": "dev04-beyond"}).json()["data"]["items"] == []

    # 不授予全局角色的账号仍可创建；之后给它改角色同样受限。
    created = _create(client, csrf, "dev04-beyond-ok", "dev04-beyond-ok", [])
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    changed = client.put(
        f"/api/v1/users/{user['id']}/roles",
        json={"expectedRevision": user["revision"], "globalRoles": ["project_creator"], "reason": "越权尝试"},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 403, changed.text
    assert changed.json()["error"]["message"] == "不能分配超出自己权限的角色。"
    _reset("dev04-usersonly")


def test_idempotent_replay_treats_missing_and_empty_roles_alike(client: TestClient) -> None:
    _reset("dev04-replay")
    csrf = _admin(client)
    first = _create(client, csrf, "dev04-replay", "dev04-replay")
    assert first.status_code == 201
    again = _create(client, csrf, "dev04-replay", "dev04-replay", [])
    assert again.status_code == 201
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    changed = _create(client, csrf, "dev04-replay", "dev04-replay", ["auditor"])
    assert changed.status_code == 409


def test_audit_records_granted_roles_including_empty(client: TestClient) -> None:
    _reset("dev04-audit-none")
    _reset("dev04-audit-admin")
    csrf = _admin(client)
    assert _create(client, csrf, "dev04-audit-none", "dev04-audit-none").status_code == 201
    assert (
        _create(client, csrf, "dev04-audit-admin", "dev04-audit-admin", ["system_admin", "auditor"]).status_code
        == 201
    )
    none_events = _events("dev04-audit-none")
    assert [e["action"] for e in none_events] == ["create"]
    assert "全局角色：无" in none_events[0]["reason"]
    admin_events = _events("dev04-audit-admin")
    actions = {e["action"]: e["reason"] for e in admin_events}
    assert "system_admin" in actions["create"] and "auditor" in actions["create"]
    assert "users.role.grant_admin" in actions


def test_role_change_revokes_target_sessions(client: TestClient) -> None:
    _reset("dev04-rolechange")
    csrf = _admin(client)
    create_user(account="dev04-rolechange", name="改角色", password=PASSWORD, roles=["auditor"])
    member = TestClient(client.app)
    _login(member, "dev04-rolechange")
    assert member.get("/api/v1/auth/me").status_code == 200
    user = client.get("/api/v1/users", params={"account": "dev04-rolechange"}).json()["data"]["items"][0]
    changed = client.put(
        f"/api/v1/users/{user['id']}/roles",
        json={"expectedRevision": user["revision"], "globalRoles": ["project_creator"], "reason": "调岗"},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200, changed.text
    assert member.get("/api/v1/auth/me").status_code == 401
    # 管理员自己的会话不受影响
    assert client.get("/api/v1/auth/me").status_code == 200
