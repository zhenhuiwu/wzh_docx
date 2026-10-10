"""Login, logout, and server-side authorization."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user

PASSWORD = "correct-horse-1"


@pytest.fixture()
def runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_ACCOUNT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_NAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    from app.config import clear_settings_cache

    clear_settings_cache()
    migrate()
    yield
    clear_settings_cache()


@pytest.fixture()
def client(runtime_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _reset(account: str) -> None:
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                """
                delete from idempotency_keys
                where user_id in (select id from users where account_key = %s)
                """,
                (account,),
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
            connection.execute(
                "delete from users where account_key = %s",
                (account,),
            )
            connection.execute(
                "delete from login_guards where account_key = %s",
                (account,),
            )


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "requestId"}
    assert set(body["data"]) == {"csrfToken"}
    return body["data"]["csrfToken"]


def _login(client: TestClient, account: str, password: str, csrf: str | None = None):
    token = _csrf(client) if csrf is None else csrf
    return client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": password},
        headers={"X-CSRF-Token": token},
    )


def test_missing_bootstrap_does_not_create_a_default_user(runtime_env) -> None:
    from app.bootstrap_admin import bootstrap_first_admin

    assert bootstrap_first_admin() is False


def test_login_logout_and_rejected_old_session(client: TestClient) -> None:
    _reset("dev03-admin")
    create_user(
        account="dev03-admin",
        name="测试管理员",
        password=PASSWORD,
        roles=["system_admin"],
    )
    wrong = _login(client, "dev03-admin", "not-the-password")
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "AUTH_REQUIRED"
    assert "not-the-password" not in wrong.text
    assert PASSWORD not in wrong.text

    missing_csrf = client.post(
        "/api/v1/auth/login",
        json={"account": "dev03-admin", "password": PASSWORD},
    )
    assert missing_csrf.status_code == 403

    signed_in = _login(client, "dev03-admin", PASSWORD)
    assert signed_in.status_code == 200
    body = signed_in.json()
    assert body["data"]["user"]["account"] == "dev03-admin"
    assert "system_admin" in body["data"]["user"]["globalRoles"]
    assert "password" not in signed_in.text.lower()
    assert "httponly" in signed_in.headers["set-cookie"].lower()
    csrf = body["data"]["csrfToken"]

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["user"]["account"] == "dev03-admin"

    logged_out = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert logged_out.status_code == 204
    assert logged_out.content == b""
    again = client.get("/api/v1/auth/me")
    assert again.status_code == 401


def test_local_storage_claim_does_not_grant_access(client: TestClient) -> None:
    _reset("dev03-auditor")
    create_user(
        account="dev03-auditor",
        name="审计",
        password=PASSWORD,
        roles=["auditor"],
    )
    forged = client.get(
        "/api/v1/auth/me",
        headers={"X-Local-Role": "system_admin"},
    )
    assert forged.status_code == 401

    signed_in = _login(client, "dev03-auditor", PASSWORD)
    assert signed_in.status_code == 200
    assert "files.read" not in signed_in.json()["data"]["permissions"]
    denied = client.get("/api/v1/files/00000000-0000-4000-8000-000000000001/content")
    # 复审 S9：读文件“不能读”与“不存在”统一 404，不泄露文件是否存在（伪造本地角色仍无效）
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "NOT_FOUND"


def test_change_password_revokes_other_sessions(client: TestClient) -> None:
    _reset("dev03-change")
    create_user(
        account="dev03-change",
        name="改密",
        password=PASSWORD,
        roles=["system_admin"],
    )
    first = _login(client, "dev03-change", PASSWORD)
    assert first.status_code == 200
    first_cookie = client.cookies.get("docwb_session")
    client.cookies.clear()
    second = _login(client, "dev03-change", PASSWORD)
    assert second.status_code == 200
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"currentPassword": PASSWORD, "newPassword": "another-secret-2"},
        headers={"X-CSRF-Token": second.json()["data"]["csrfToken"]},
    )
    assert changed.status_code == 204
    assert PASSWORD not in changed.text
    client.cookies.clear()
    client.cookies.set("docwb_session", first_cookie)
    old = client.get("/api/v1/auth/me")
    assert old.status_code == 401
    with connect() as connection:
        connection.execute("delete from login_guards where account_key = 'dev03-change'")
        connection.commit()
    client.cookies.clear()
    resigned = _login(client, "dev03-change", "another-secret-2")
    assert resigned.status_code == 200


def test_wrong_current_password_has_stable_code_and_is_limited(client: TestClient) -> None:
    _reset("dev03-guess")
    create_user(account="dev03-guess", name="猜测", password=PASSWORD, roles=["project_creator"])
    signed = _login(client, "dev03-guess", PASSWORD)
    csrf = signed.json()["data"]["csrfToken"]

    def attempt(current: str):
        return client.post(
            "/api/v1/auth/change-password",
            json={"currentPassword": current, "newPassword": "guessed-secret-9"},
            headers={"X-CSRF-Token": csrf},
        )

    for _ in range(8):
        wrong = attempt("not-the-password")
        assert wrong.status_code == 422, wrong.text
        assert wrong.json()["error"]["code"] == "CURRENT_PASSWORD_INCORRECT"
        assert wrong.json()["error"]["fieldErrors"][0]["field"] == "currentPassword"
    assert client.get("/api/v1/auth/me").status_code == 200
    limited = attempt(PASSWORD)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "LIMIT_EXCEEDED"
    assert 1 <= int(limited.headers["Retry-After"]) <= 600
    # The change-password counter does not lock the account out of signing in.
    other = TestClient(client.app)
    assert _login(other, "dev03-guess", PASSWORD).status_code == 200
    with connect() as connection:
        from app.identity import PASSWORD_CHANGE_GUARD_PREFIX

        keys = [row["account_key"] for row in connection.execute("select account_key from login_guards")]
        assert "dev03-guess" not in keys
        assert any(key.startswith(PASSWORD_CHANGE_GUARD_PREFIX) for key in keys)
        connection.execute("delete from login_guards where substr(account_key, 1, %s) = %s",
                           (len(PASSWORD_CHANGE_GUARD_PREFIX), PASSWORD_CHANGE_GUARD_PREFIX))
        connection.commit()
    assert attempt(PASSWORD).status_code == 204


def test_login_lockout_sends_retry_after(client: TestClient) -> None:
    _reset("dev03-lock")
    create_user(account="dev03-lock", name="锁定", password=PASSWORD, roles=["project_creator"])
    for _ in range(8):
        client.cookies.clear()
        assert _login(client, "dev03-lock", "wrong-password-00").status_code == 401
    client.cookies.clear()
    locked = _login(client, "dev03-lock", PASSWORD)
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "LIMIT_EXCEEDED"
    assert 1 <= int(locked.headers["Retry-After"]) <= 600


def test_disabled_account_cannot_login(client: TestClient) -> None:
    _reset("dev03-disabled")
    user_id = create_user(
        account="dev03-disabled",
        name="停用",
        password=PASSWORD,
        roles=["auditor"],
    )
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                "update users set status = 'disabled' where id = %s",
                (user_id,),
            )
    response = _login(client, "dev03-disabled", PASSWORD)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert PASSWORD not in response.text


def test_bootstrap_admin_must_change_password_before_other_apis(runtime_env, monkeypatch) -> None:
    from app.bootstrap_admin import bootstrap_first_admin
    from app.main import create_app

    monkeypatch.setenv("BOOTSTRAP_ADMIN_ACCOUNT", "first-admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_NAME", "第一管理员")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "initial-secret-1")
    assert bootstrap_first_admin() is True
    with TestClient(create_app()) as client:
        response = _login(client, "first-admin", "initial-secret-1")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["user"]["requiresFirstLoginChange"] is True
        csrf = response.json()["data"]["csrfToken"]
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["data"]["user"]["requiresFirstLoginChange"] is True
        for path in ("/api/v1/projects", "/api/v1/users", "/api/v1/templates"):
            blocked = client.get(path)
            assert blocked.status_code == 403, path
            assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"currentPassword": "initial-secret-1", "newPassword": "changed-secret-2"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 204, changed.text
        me = client.get("/api/v1/auth/me")
        assert me.json()["data"]["user"]["requiresFirstLoginChange"] is False
        assert client.get("/api/v1/projects").status_code == 200


def test_known_default_password_is_flagged_for_change(runtime_env) -> None:
    from app.bootstrap_admin import flag_known_default_passwords
    from app.identity import hash_password

    for account in ("admin", "dev03-default", "dev03-strong"):
        _reset(account)
        create_user(account=account, name=account, password=PASSWORD, roles=["project_creator"])
    flag_known_default_passwords()
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                "update users set password_hash = %s where account_key in ('admin', 'dev03-default')",
                (hash_password("admin123"),),
            )
    assert flag_known_default_passwords() >= 2
    with connect() as connection:
        rows = {
            row["account_key"]: bool(row["must_change_password"])
            for row in connection.execute(
                "select account_key, must_change_password from users "
                "where account_key in ('admin', 'dev03-default', 'dev03-strong')"
            )
        }
    assert rows == {"admin": True, "dev03-default": True, "dev03-strong": False}
    assert flag_known_default_passwords() == 0
