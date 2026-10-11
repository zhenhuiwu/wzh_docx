"""Object-level project, knowledge, and file checks."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.files import store_bytes
from app.identity import create_user

PASSWORD = "correct-horse-1"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch, isolated_test_storage):
    monkeypatch.delenv("BOOTSTRAP_ADMIN_ACCOUNT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    from app.config import clear_settings_cache

    clear_settings_cache()
    from app.db import migrate
    from app.main import create_app

    migrate()
    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _user(account: str, roles: list[str]) -> str:
    return str(create_user(account=account, name=account, password=PASSWORD, roles=roles))


def _login(client: TestClient, account: str) -> str:
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def test_project_creator_viewer_cannot_patch(client: TestClient) -> None:
    owner = _user("scope-owner", ["project_creator"])
    viewer = _user("scope-viewer", ["project_creator"])
    csrf = _login(client, "scope-owner")
    created = client.post(
        "/api/v1/projects",
        json={"name": "范围项目", "ownerId": owner, "status": "not_started"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "scope-project"},
    )
    assert created.status_code == 201, created.text
    project = created.json()["data"]
    added = client.post(
        f"/api/v1/projects/{project['id']}/members:batch",
        json={"userIds": [viewer], "role": "viewer", "expectedMembersRevision": project["membersRevision"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert added.status_code == 200, added.text
    other = TestClient(client.app)
    other_csrf = _login(other, "scope-viewer")
    visible = other.get(f"/api/v1/projects/{project['id']}")
    assert visible.status_code == 200, visible.text
    patched = other.patch(
        f"/api/v1/projects/{project['id']}",
        json={"expectedRevision": project["revision"], "name": "被抢走"},
        headers={"X-CSRF-Token": other_csrf},
    )
    assert patched.status_code == 403
    preserved = client.get(f"/api/v1/projects/{project['id']}")
    assert preserved.status_code == 200
    assert preserved.json()["data"]["name"] == "范围项目"
    # A05 R5：成员变更不再递增项目 revision
    assert preserved.json()["data"]["revision"] == project["revision"]


def test_knowledge_reader_cannot_rename(client: TestClient) -> None:
    owner = _user("scope-k-owner", ["knowledge_manager"])
    reader = _user("scope-k-reader", ["knowledge_manager"])
    csrf = _login(client, "scope-k-owner")
    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "范围库", "summary": "", "scope": "organization", "ownerId": owner},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    base = created.json()["data"]
    opened = client.put(
        f"/api/v1/knowledge-bases/{base['id']}/access",
        json={"expectedRevision": base["revision"], "readers": [reader], "maintainers": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert opened.status_code == 200, opened.text
    other = TestClient(client.app)
    other_csrf = _login(other, "scope-k-reader")
    visible = other.get(f"/api/v1/knowledge-bases/{base['id']}")
    assert visible.status_code == 200, visible.text
    renamed = other.patch(
        f"/api/v1/knowledge-bases/{base['id']}",
        json={"expectedRevision": opened.json()["data"]["revision"], "name": "改掉的库"},
        headers={"X-CSRF-Token": other_csrf},
    )
    assert renamed.status_code == 403
    with connect() as connection:
        preserved = connection.execute("SELECT name, revision FROM knowledge_bases WHERE id = %s", (base["id"],)).fetchone()
    assert preserved["name"] == "范围库"
    assert preserved["revision"] == opened.json()["data"]["revision"]


def test_unscoped_file_is_not_readable(client: TestClient) -> None:
    record = store_bytes(
        name="unscoped.txt",
        media_type="text/plain",
        purpose="attachment",
        content=b"secret-body",
        project_id=None,
        base_id=None,
    )
    _user("scope-file", ["knowledge_manager"])
    _login(client, "scope-file")
    denied = client.get(f"/api/v1/files/{record.id}/content")
    assert denied.status_code == 404
    assert b"secret-body" not in denied.content
    _user("scope-file-admin", ["system_admin"])
    admin = TestClient(client.app)
    _login(admin, "scope-file-admin")
    opened = admin.get(f"/api/v1/files/{record.id}/content")
    assert opened.status_code == 200
    assert opened.content == b"secret-body"
    assert uuid.UUID(str(record.id))
