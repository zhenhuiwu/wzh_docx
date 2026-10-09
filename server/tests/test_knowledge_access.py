"""Knowledge lists and downloads follow the current access list."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.files import store_bytes
from app.identity import create_user

PASSWORD = "correct-horse-1"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    from app.config import clear_settings_cache

    clear_settings_cache()
    migrate()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _login(client: TestClient, account: str) -> str:
    token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": PASSWORD},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def _reset(account: str) -> None:
    with connect() as connection:
        with connection.transaction():
            connection.execute("delete from source_versions")
            connection.execute("delete from sources")
            connection.execute("delete from knowledge_access")
            connection.execute("delete from knowledge_bases")
            connection.execute(
                "delete from sessions where user_id in (select id from users where account_key = %s)",
                (account,),
            )
            connection.execute(
                "delete from user_roles where user_id in (select id from users where account_key = %s)",
                (account,),
            )
            connection.execute("delete from users where account_key = %s", (account,))


def test_hidden_base_does_not_leak_and_versions_do_not_overwrite(client: TestClient) -> None:
    _reset("dev09-admin")
    _reset("dev09-other")
    admin_id = str(create_user(account="dev09-admin", name="知识管理员", password=PASSWORD, roles=["system_admin"]))
    other_id = str(create_user(account="dev09-other", name="其他维护者", password=PASSWORD, roles=["knowledge_manager"]))
    csrf = _login(client, "dev09-admin")
    project = client.post(
        "/api/v1/projects",
        json={"name": "甲项目", "ownerId": admin_id, "status": "active"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "dev09-project"},
    )
    assert project.status_code == 201, project.text
    missing_project = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "会泄漏的库", "scope": "project", "projectId": None, "ownerId": admin_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_project.status_code == 422
    created = client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": "甲项目资料",
            "summary": "只给授权的人",
            "scope": "project",
            "projectId": project.json()["data"]["id"],
            "ownerId": admin_id,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    base_id = created.json()["data"]["id"]
    other = TestClient(client.app)
    other_csrf = _login(other, "dev09-other")
    hidden = other.get("/api/v1/knowledge-bases")
    assert hidden.status_code == 200
    assert hidden.json()["data"]["total"] == 0
    assert "甲项目资料" not in hidden.text
    assert other.get(f"/api/v1/knowledge-bases/{base_id}").status_code == 404
    first = store_bytes(name="一版.txt", media_type="text/plain", purpose="source", content=b"one", base_id=__import__("uuid").UUID(base_id))
    second = store_bytes(name="二版.txt", media_type="text/plain", purpose="source", content=b"two", base_id=__import__("uuid").UUID(base_id))
    source = client.post(
        f"/api/v1/knowledge-bases/{base_id}/sources",
        json={"name": "接口说明", "fileId": str(first.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert source.status_code == 201, source.text
    assert source.json()["data"]["version"] == "1"
    assert source.json()["data"]["status"] == "uploaded"
    assert "不能检索" in source.json()["data"]["message"]
    duplicate = client.post(
        f"/api/v1/knowledge-bases/{base_id}/sources",
        json={"name": "接口说明", "fileId": str(second.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    newer = client.post(
        f"/api/v1/sources/{source.json()['data']['sourceId']}/versions",
        json={"fileId": str(second.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert newer.status_code == 201, newer.text
    assert newer.json()["data"]["version"] == "2"
    still = client.get(f"/api/v1/source-versions/{source.json()['data']['id']}")
    assert still.status_code == 200
    assert still.json()["data"]["version"] == "1"
    denied = other.get(f"/api/v1/files/{first.id}/content")
    assert denied.status_code == 404
    assert b"one" not in denied.content
    opened = client.put(
        f"/api/v1/knowledge-bases/{base_id}/access",
        json={"expectedRevision": created.json()["data"]["revision"], "readers": [other_id], "maintainers": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert opened.status_code == 200, opened.text
    assert other.get(f"/api/v1/knowledge-bases/{base_id}").status_code == 200
    closed = client.put(
        f"/api/v1/knowledge-bases/{base_id}/access",
        json={"expectedRevision": opened.json()["data"]["revision"], "readers": [], "maintainers": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert closed.status_code == 200, closed.text
    assert other.get(f"/api/v1/knowledge-bases/{base_id}").status_code == 404
    assert other.get(f"/api/v1/files/{first.id}/content").status_code == 404
    assert other_csrf
