"""Project visibility, paging, and the owner member."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user

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
                delete from project_members
                where user_id in (select id from users where account_key = %s)
                   or project_id in (
                     select id from projects
                     where owner_id in (select id from users where account_key = %s)
                   )
                """,
                (account, account),
            )
            connection.execute(
                """
                delete from idempotency_keys
                where user_id in (select id from users where account_key = %s)
                """,
                (account,),
            )
            connection.execute(
                """
                delete from projects
                where owner_id in (select id from users where account_key = %s)
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
            connection.execute("delete from users where account_key = %s", (account,))


def _login(client: TestClient, account: str) -> str:
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def _user(account: str) -> str:
    _reset(account)
    return str(
        create_user(
            account=account,
            name=account,
            password=PASSWORD,
            roles=["project_creator"],
        )
    )


def _create(client: TestClient, csrf: str, owner_id: str, name: str, department: str, key: str):
    response = client.post(
        "/api/v1/projects",
        json={
            "name": name,
            "department": department,
            "ownerId": owner_id,
            "status": "not_started",
        },
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_isolated_projects_do_not_leak(client: TestClient) -> None:
    owner_a = _user("dev05-a")
    owner_b = _user("dev05-b")
    csrf_a = _login(client, "dev05-a")
    project_a = _create(client, csrf_a, owner_a, "甲项目", "软件", "dev05-a-1")
    other = TestClient(client.app)
    csrf_b = _login(other, "dev05-b")
    project_b = _create(other, csrf_b, owner_b, "乙项目", "总体", "dev05-b-1")
    listed = client.get("/api/v1/projects")
    assert listed.status_code == 200
    body = listed.json()["data"]
    assert body["total"] == 1
    assert [item["name"] for item in body["items"]] == ["甲项目"]
    assert project_b["name"] not in listed.text
    hidden = client.get(f"/api/v1/projects/{project_b['id']}")
    assert hidden.status_code == 404
    visible = client.get(f"/api/v1/projects/{project_a['id']}")
    assert visible.status_code == 200
    assert visible.json()["data"]["ownerId"] == owner_a


def test_and_query_and_second_page(client: TestClient) -> None:
    owner = _user("dev05-page")
    csrf = _login(client, "dev05-page")
    _create(client, csrf, owner, "导航软件", "软件", "dev05-page-nav")
    _create(client, csrf, owner, "导航总体", "总体", "dev05-page-all")
    for index in range(10):
        _create(client, csrf, owner, f"分页项目{index}", "测试", f"dev05-page-{index}")
    both = client.get("/api/v1/projects", params={"name": "导航", "department": "软件"})
    assert both.status_code == 200
    matched = both.json()["data"]
    assert matched["total"] == 1
    assert matched["items"][0]["name"] == "导航软件"
    page2 = client.get("/api/v1/projects", params={"page": 2, "pageSize": 10})
    assert page2.status_code == 200
    assert page2.json()["data"]["total"] == 12
    assert len(page2.json()["data"]["items"]) == 2


def test_last_owner_cannot_be_removed(client: TestClient) -> None:
    owner = _user("dev05-owner")
    csrf = _login(client, "dev05-owner")
    project = _create(client, csrf, owner, "负责人项目", "软件", "dev05-owner-1")
    removed = client.delete(
        f"/api/v1/projects/{project['id']}/members/{owner}",
        params={"expectedMembersRevision": project["membersRevision"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 409
    # A05 B-4：最后一位启用负责人 → 409 LAST_OWNER（原为笼统的 INVALID_TRANSITION）
    assert removed.json()["error"]["code"] == "LAST_OWNER"
