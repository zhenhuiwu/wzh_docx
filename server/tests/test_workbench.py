"""The workbench follows real reviews, facts and jobs, and hides other projects."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user
from app.worker import enqueue

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


def _csrf(client: TestClient) -> str:
    return client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]


def _wipe(account: str) -> None:
    with connect() as connection:
        with connection.transaction():
            owned = "(select id from users where account_key = %s)"
            connection.execute(f"delete from job_steps where job_id in (select id from jobs where created_by in {owned})", (account,))
            connection.execute(f"delete from job_events where job_id in (select id from jobs where created_by in {owned})", (account,))
            connection.execute(f"delete from jobs where created_by in {owned}", (account,))
            connection.execute(
                f"""
                delete from fact_sources
                where revision_id in (
                  select r.id from fact_revisions r
                  join facts f on f.id = r.fact_id
                  where f.project_id in (select id from projects where owner_id in {owned})
                )
                """,
                (account,),
            )
            connection.execute(
                f"""
                delete from fact_revisions
                where fact_id in (
                  select id from facts
                  where project_id in (select id from projects where owner_id in {owned})
                )
                """,
                (account,),
            )
            connection.execute(
                f"delete from facts where project_id in (select id from projects where owner_id in {owned})",
                (account,),
            )
            connection.execute(
                f"""
                delete from project_members
                where user_id in {owned} or project_id in (select id from projects where owner_id in {owned})
                """,
                (account, account),
            )
            connection.execute(f"delete from projects where owner_id in {owned}", (account,))
            connection.execute(f"delete from idempotency_keys where user_id in {owned}", (account,))
            connection.execute(f"delete from sessions where user_id in {owned}", (account,))
            connection.execute(f"delete from user_roles where user_id in {owned}", (account,))
            connection.execute("delete from users where account_key = %s", (account,))


def _login(client: TestClient, account: str, roles: list[str]) -> tuple[str, str]:
    _wipe(account)
    user_id = create_user(account=account, name=account, password=PASSWORD, roles=roles)
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": PASSWORD},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 200, response.text
    return str(user_id), response.json()["data"]["csrfToken"]


def test_empty_workbench_is_not_an_error_and_hides_other_projects(client: TestClient) -> None:
    admin_id, csrf = _login(client, "dev17-admin", ["system_admin"])
    project = client.post(
        "/api/v1/projects",
        json={"name": "试航文档", "ownerId": admin_id, "status": "active"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "dev17-project"},
    )
    assert project.status_code == 201, project.text
    board = client.get("/api/v1/workbench")
    assert board.status_code == 200, board.text
    data = board.json()["data"]
    assert data["issues"] == []
    assert any(item["name"] == "试航文档" for item in data["recentProjects"])
    assert data["activeJobs"] == []
    issues = client.get("/api/v1/issues")
    assert issues.status_code == 200, issues.text
    assert issues.json()["data"]["total"] == 0
    outsider = TestClient(client.app)
    _login(outsider, "dev17-other", ["project_creator"])
    hidden = outsider.get("/api/v1/workbench")
    assert hidden.status_code == 200, hidden.text
    assert "试航文档" not in hidden.text
    assert outsider.get("/api/v1/issues").json()["data"]["items"] == []


def test_confirming_a_fact_removes_its_issue_and_a_job_stays_visible(client: TestClient) -> None:
    admin_id, csrf = _login(client, "dev17-admin", ["system_admin"])
    project = client.post(
        "/api/v1/projects",
        json={"name": "待办项目", "ownerId": admin_id, "status": "active"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "dev17-project-2"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["data"]["id"]
    fact = client.post(
        f"/api/v1/projects/{project_id}/facts",
        json={"key": "ship-name", "label": "船名", "value": "试航一号", "decisionReason": "人工登记"},
        headers={"X-CSRF-Token": csrf},
    )
    assert fact.status_code == 201, fact.text
    enqueue(
        created_by=UUID(admin_id),
        kind="check_consistency",
        target_kind="system",
        target_name="一致性检查",
        step_plan="pure:one",
        project_id=UUID(project_id),
    )
    listed = client.get(f"/api/v1/issues?projectId={project_id}&kind=fact_confirmation")
    assert listed.status_code == 200, listed.text
    body = listed.json()["data"]
    assert body["total"] == 1
    issue = body["items"][0]
    assert issue["title"] == "待确认船名"
    assert issue["status"] == "open"
    assert issue["target"]["name"] == "船名"
    assert issue["projectName"] == "待办项目"
    board = client.get("/api/v1/workbench").json()["data"]
    assert any(item["target"]["name"] == "一致性检查" for item in board["activeJobs"])
    confirmed = client.post(
        f"/api/v1/facts/{fact.json()['data']['id']}/confirm",
        json={"expectedRevision": fact.json()["data"]["revision"], "reason": "确认"},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200, confirmed.text
    after = client.get(f"/api/v1/issues?projectId={project_id}&kind=fact_confirmation")
    assert after.json()["data"]["total"] == 0
    still = client.get("/api/v1/workbench").json()["data"]
    assert any(item["name"] == "待办项目" for item in still["recentProjects"])
    assert any(item["target"]["name"] == "一致性检查" for item in still["activeJobs"])
