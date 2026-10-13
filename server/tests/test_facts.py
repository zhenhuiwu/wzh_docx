"""Confirmed facts keep the old value, and hidden knowledge bases cannot be copied in."""

from __future__ import annotations

import os
from io import BytesIO
from uuid import UUID

from docx import Document
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


def _user(account: str, roles: list[str]) -> str:
    # Every test has an isolated SQLite database; obsolete table cleanup is unnecessary.
    return str(create_user(account=account, name=account, password=PASSWORD, roles=roles))


def test_confirming_a_change_keeps_the_old_value(client: TestClient) -> None:
    admin_id = _user("dev06-admin", ["system_admin"])
    csrf = _login(client, "dev06-admin")
    project = client.post(
        "/api/v1/projects",
        json={"name": "事实项目", "ownerId": admin_id, "status": "active"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "dev06-project"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]
    word = Document()
    word.add_heading("范围", level=1)
    word.add_paragraph("中性事实文档正文。")
    buffer = BytesIO()
    word.save(buffer)
    stored = store_bytes(name="事实说明.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         purpose="attachment", project_id=UUID(project_id), content=buffer.getvalue())
    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        json={"name": "说明", "sourceType": "upload", "wordFileId": str(stored.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert document.status_code == 200, document.text
    created = client.post(
        f"/api/v1/projects/{project_id}/facts",
        json={"key": "software_version", "label": "软件版本", "value": "1.0", "decisionReason": "人工登记"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    fact_id = created.json()["data"]["id"]
    first = client.post(
        f"/api/v1/facts/{fact_id}/confirm",
        json={"expectedRevision": created.json()["data"]["revision"], "reason": "确认 1.0"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200, first.text
    revised = client.patch(
        f"/api/v1/facts/{fact_id}",
        json={"expectedRevision": first.json()["data"]["revision"], "value": "1.1", "decisionReason": "版本调整"},
        headers={"X-CSRF-Token": csrf},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["data"]["state"] == "candidate"
    current = client.get(f"/api/v1/projects/{project_id}/facts")
    assert current.status_code == 200, current.text
    current_body = current.json()["data"]
    assert current_body["page"] == 1
    assert current_body["pageSize"] == 10
    assert current_body["total"] == 1
    assert current_body["items"][0]["value"] == "1.1"
    assert current_body["items"][0]["state"] == "candidate"
    baseline = client.get(f"/api/v1/projects/{project_id}/facts?state=confirmed")
    assert baseline.json()["data"]["items"][0]["value"] == "1.0"
    assert baseline.json()["data"]["items"][0]["state"] == "confirmed"
    second = client.post(
        f"/api/v1/facts/{fact_id}/confirm",
        json={"expectedRevision": revised.json()["data"]["revision"], "reason": "确认 1.1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["value"] == "1.1"
    with connect() as connection:
        old = connection.execute(
            "select value_text, state from fact_revisions where fact_id = %s and value_text = '1.0'",
            (fact_id,),
        ).fetchone()
        doc = connection.execute("select validity from documents where id = %s", (document.json()["data"]["id"],)).fetchone()
        software = connection.execute("select software_version from projects where id = %s", (project_id,)).fetchone()
    assert old["state"] == "superseded"
    assert old["value_text"] == "1.0"
    assert doc["validity"] == "needs_review"
    assert software["software_version"] == "1.1"
    kept = client.get(f"/api/v1/projects/{project_id}/facts?state=superseded")
    assert kept.json()["data"]["items"][0]["value"] == "1.0"
    assert kept.json()["data"]["items"][0]["state"] == "superseded"
    latest = client.get(f"/api/v1/projects/{project_id}/facts")
    assert latest.json()["data"]["items"][0]["value"] == "1.1"
    assert latest.json()["data"]["items"][0]["state"] == "confirmed"


def test_hidden_knowledge_base_is_not_copied(client: TestClient) -> None:
    admin_id = _user("dev06-owner", ["system_admin"])
    other_id = _user("dev06-other", ["project_creator"])
    csrf = _login(client, "dev06-owner")
    project = client.post(
        "/api/v1/projects",
        json={"name": "绑定项目", "ownerId": admin_id, "status": "active"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "dev06-bind"},
    )
    project_id = project.json()["data"]["id"]
    secret = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "保密库", "scope": "organization", "projectId": None, "ownerId": admin_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert secret.status_code == 201, secret.text
    base_id = secret.json()["data"]["id"]
    stored = store_bytes(name="密.txt", media_type="text/plain", purpose="source", content=b"secret", base_id=__import__("uuid").UUID(base_id))
    source = client.post(
        f"/api/v1/knowledge-bases/{base_id}/sources",
        json={"name": "密件", "fileId": str(stored.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert source.status_code == 201, source.text
    unbound = client.post(
        f"/api/v1/projects/{project_id}/facts",
        json={
            "key": "from-secret",
            "label": "不该写入",
            "value": "x",
            "sourceVersionIds": [source.json()["data"]["id"]],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert unbound.status_code == 404
    assert "保密库" not in unbound.text
    assert "密件" not in unbound.text
    member = client.post(
        f"/api/v1/projects/{project_id}/members:batch",
        json={"userIds": [other_id], "role": "owner", "expectedMembersRevision": project.json()["data"]["membersRevision"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert member.status_code == 200, member.text
    other = TestClient(client.app)
    other_csrf = _login(other, "dev06-other")
    bound = other.put(
        f"/api/v1/projects/{project_id}/knowledge-bindings",
        json={"expectedProjectRevision": project.json()["data"]["revision"], "baseIds": [base_id]},  # A05：成员变更不再加项目 revision
        headers={"X-CSRF-Token": other_csrf},
    )
    assert bound.status_code == 404
    listed = other.get(f"/api/v1/projects/{project_id}/knowledge-bindings")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []
    assert "保密库" not in listed.text
    fact = other.post(
        f"/api/v1/projects/{project_id}/facts",
        json={
            "key": "leaked",
            "label": "泄露",
            "value": "no",
            "sourceVersionIds": [source.json()["data"]["id"]],
        },
        headers={"X-CSRF-Token": other_csrf},
    )
    assert fact.status_code == 404
    with connect() as connection:
        copied = connection.execute(
            "select count(*) as total from facts where fact_key in ('leaked', 'from-secret')"
        ).fetchone()
    assert copied["total"] == 0


def test_non_member_writes_get_404_member_without_permission_gets_403(client: TestClient) -> None:
    """终验可顺手做：非成员写事实/知识库关联返回 404（不暴露项目存在），成员但无权限返回 403。"""
    admin_id = _user("facts-404-admin", ["system_admin"])
    csrf = _login(client, "facts-404-admin")
    project = client.post("/api/v1/projects", json={"name": "事实权限", "ownerId": admin_id, "status": "active"},
                          headers={"X-CSRF-Token": csrf, "Idempotency-Key": "facts-404-project"})
    project_id = project.json()["data"]["id"]
    created = client.post(f"/api/v1/projects/{project_id}/facts",
                          json={"key": "software_version", "label": "软件版本", "value": "1.0", "decisionReason": "人工登记"},
                          headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201, created.text
    fact = created.json()["data"]
    member = str(create_user(account="facts-404-member", name="成员", password=PASSWORD, roles=[], allow_no_roles=True))
    members_revision = client.get(f"/api/v1/projects/{project_id}/members").json()["data"]["membersRevision"]
    added = client.post(f"/api/v1/projects/{project_id}/members:batch",
                        json={"userIds": [member], "role": "editor", "expectedMembersRevision": members_revision}, headers={"X-CSRF-Token": csrf})
    assert added.status_code == 200, added.text
    project_revision = client.get(f"/api/v1/projects/{project_id}").json()["data"]["revision"]
    create_user(account="facts-404-outsider", name="外人", password=PASSWORD, roles=[], allow_no_roles=True)
    create_user(account="facts-404-creator", name="创建者", password=PASSWORD, roles=["project_creator"])

    def writes(token: str) -> list[int]:
        headers = {"X-CSRF-Token": token}
        return [
            client.post(f"/api/v1/projects/{project_id}/facts", json={"key": "k2", "label": "名称", "value": "v", "decisionReason": "r"}, headers=headers).status_code,
            client.patch(f"/api/v1/facts/{fact['id']}", json={"expectedRevision": fact["revision"], "value": "2", "decisionReason": "r"}, headers=headers).status_code,
            client.post(f"/api/v1/facts/{fact['id']}/confirm", json={"expectedRevision": fact["revision"], "reason": "r"}, headers=headers).status_code,
            client.put(f"/api/v1/projects/{project_id}/knowledge-bindings", json={"baseIds": [], "expectedProjectRevision": project_revision}, headers=headers).status_code,
        ]

    assert writes(_login(client, "facts-404-outsider")) == [404, 404, 404, 404]
    assert writes(_login(client, "facts-404-creator")) == [404, 404, 404, 404]
    assert writes(_login(client, "facts-404-member")) == [403, 403, 403, 403]
