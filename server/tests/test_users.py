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
