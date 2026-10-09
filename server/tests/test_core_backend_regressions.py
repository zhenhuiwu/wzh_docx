"""SQLite core flows: concurrent writes, usable accounts, and scoped uploads."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import clear_settings_cache, get_settings
from app.db import DictConnection, connect, migrate
from app.identity import create_user, hash_password, verify_password

PASSWORD = "correct-horse-1"
NEW_PASSWORD = "replacement-pass-2"


@pytest.fixture()
def client():
    from app.main import create_app

    clear_settings_cache()
    migrate()
    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _login(client, account, password=PASSWORD):
    token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post("/api/v1/auth/login", json={"account": account, "password": password}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def _user(account, roles=None):
    return create_user(account=account, name=account, password=PASSWORD, roles=roles or ["system_admin"])


def _project(client, csrf, owner, name="测试项目", key=None):
    response = client.post("/api/v1/projects", json={"name": name, "ownerId": str(owner), "status": "not_started"}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _parallel(*calls):
    barrier = Barrier(len(calls))
    def run(call):
        barrier.wait(timeout=5)
        return call()
    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return list(executor.map(run, calls))


@pytest.mark.parametrize("resource", ["users", "projects", "templates"])
def test_parallel_expected_revision_allows_only_one_save(client, monkeypatch, resource):
    owner = _user("core-admin")
    csrf = _login(client, "core-admin")
    other = TestClient(client.app)
    other_csrf = _login(other, "core-admin")
    if resource == "users":
        target = str(owner)
        from app import users
        check = users._check_revision
        def slow_check(*args):
            check(*args)
            time.sleep(.05)
        monkeypatch.setattr(users, "_check_revision", slow_check)
    elif resource == "projects":
        target = _project(client, csrf, owner)["id"]
        from app import projects
        check = projects._check_revision
        def slow_check(*args):
            check(*args)
            time.sleep(.05)
        monkeypatch.setattr(projects, "_check_revision", slow_check)
    else:
        response = client.post("/api/v1/templates", json={"name": "原模板", "category": "文档"}, headers={"X-CSRF-Token": csrf})
        target = response.json()["data"]["id"]
        execute = DictConnection.execute
        def slow_read(connection, sql, params=()):
            result = execute(connection, sql, params)
            if "SELECT revision FROM templates" in sql:
                time.sleep(.05)
            return result
        monkeypatch.setattr(DictConnection, "execute", slow_read)
    url = f"/api/v1/{resource}/{target}"
    results = _parallel(
        lambda: client.patch(url, json={"expectedRevision": 0, "name": "并发甲"}, headers={"X-CSRF-Token": csrf}),
        lambda: other.patch(url, json={"expectedRevision": 0, "name": "并发乙"}, headers={"X-CSRF-Token": other_csrf}),
    )
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    winner = next(response.json()["data"] for response in results if response.status_code == 200)
    with connect() as connection:
        saved = connection.execute(f"SELECT revision, name FROM {resource} WHERE id = %s", (target,)).fetchone()
    assert saved["revision"] == 1 and saved["name"] == winner["name"]


def test_two_admins_cannot_both_disable_themselves(client, monkeypatch):
    first = _user("first-admin")
    second = _user("second-admin")
    csrf = _login(client, "first-admin")
    other = TestClient(client.app)
    other_csrf = _login(other, "second-admin")
    from app import users
    reject = users._reject_last_admin
    def slow_reject(*args, **kwargs):
        reject(*args, **kwargs)
        time.sleep(.05)
    monkeypatch.setattr(users, "_reject_last_admin", slow_reject)
    results = _parallel(
        lambda: client.post(f"/api/v1/users/{first}/disable", json={"expectedRevision": 0, "reason": "停用"}, headers={"X-CSRF-Token": csrf}),
        lambda: other.post(f"/api/v1/users/{second}/disable", json={"expectedRevision": 0, "reason": "停用"}, headers={"X-CSRF-Token": other_csrf}),
    )
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    with connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS total FROM users u JOIN user_roles r ON r.user_id=u.id WHERE u.status='active' AND r.role='system_admin'").fetchone()
    assert count["total"] == 1


def test_create_and_reset_passwords_are_usable_and_never_returned(client):
    _user("core-admin")
    csrf = _login(client, "core-admin")
    create_payload = {"account": "new-user", "name": "新用户", "globalRoles": ["project_creator"], "password": PASSWORD}
    response = client.post("/api/v1/users", json=create_payload, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "new-user"})
    assert response.status_code == 201, response.text
    user_id = response.json()["data"]["id"]
    assert PASSWORD not in response.text
    member = TestClient(client.app)
    _login(member, "new-user")
    assert member.get("/api/v1/auth/me").status_code == 200
    reset = client.post(f"/api/v1/users/{user_id}/reset-credentials", json={"expectedRevision": 0, "reason": "重新设定", "password": NEW_PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert reset.status_code == 200, reset.text
    assert NEW_PASSWORD not in reset.text and not reset.json()["data"]["requiresFirstLoginChange"]
    assert member.get("/api/v1/auth/me").status_code == 401
    _login(member, "new-user", NEW_PASSWORD)
    assert member.get("/api/v1/auth/me").status_code == 200
    with connect() as connection:
        stored = connection.execute("SELECT password_hash, must_change_password FROM users WHERE id = %s", (user_id,)).fetchone()
    assert verify_password(NEW_PASSWORD, stored["password_hash"]) and not stored["must_change_password"]
    for payload in [{"expectedRevision": 1, "reason": "缺少密码"}, {"expectedRevision": 1, "reason": "太短", "password": "short"}]:
        assert client.post(f"/api/v1/users/{user_id}/reset-credentials", json=payload, headers={"X-CSRF-Token": csrf}).status_code == 422
    assert member.get("/api/v1/auth/me").status_code == 200


def test_existing_fixed_admin_credentials_can_login(client):
    admin_id = _user("admin")
    with connect() as connection:
        with connection.transaction():
            connection.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password("admin123"), admin_id))
    _login(client, "admin", "admin123")
    assert client.get("/api/v1/auth/me").json()["data"]["user"]["account"] == "admin"


def test_project_creator_can_choose_self_create_and_read_without_users_permission(client):
    owner = _user("project-owner", ["project_creator"])
    other = _user("other-owner", ["project_creator"])
    csrf = _login(client, "project-owner")
    assert client.get("/api/v1/users").status_code == 403
    response = client.get("/api/v1/project-owners")
    assert response.status_code == 200, response.text
    choices = response.json()["data"]
    assert choices["total"] == 1 and choices["items"] == [{"id": str(owner), "name": "project-owner", "account": "project-owner"}]
    project = _project(client, csrf, owner)
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 200
    denied = client.post("/api/v1/projects", json={"name": "越权负责人", "ownerId": str(other), "status": "not_started"}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "other-owner"})
    assert denied.status_code == 403
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM projects").fetchone()["total"] == 1


def test_project_owner_choices_for_admin_are_paginated_and_minimal(client):
    _user("admin")
    _user("member-a", ["project_creator"])
    disabled = _user("member-b", ["project_creator"])
    with connect() as connection:
        with connection.transaction():
            connection.execute("UPDATE users SET status='disabled' WHERE id = %s", (disabled,))
    _login(client, "admin")
    first = client.get("/api/v1/project-owners", params={"page": 1, "pageSize": 1}).json()["data"]
    second = client.get("/api/v1/project-owners", params={"page": 2, "pageSize": 1}).json()["data"]
    assert first["total"] == second["total"] == 2
    assert set(first["items"][0]) == {"id", "name", "account"}
    assert first["items"][0]["id"] != second["items"][0]["id"]
    for params in [{"page": 0}, {"pageSize": 0}, {"page": "bad"}, {"pageSize": 51}]:
        assert client.get("/api/v1/project-owners", params=params).status_code == 422


def test_upload_scope_and_idempotency_include_metadata(client):
    owner = _user("core-owner", ["project_creator"])
    outsider = _user("core-outsider", ["project_creator"])
    csrf = _login(client, "core-owner")
    first = _project(client, csrf, owner)
    second = _project(client, csrf, owner, "第二项目")
    other = TestClient(client.app)
    other_csrf = _login(other, "core-outsider")
    def upload(browser, token, project=first["id"], key="same-upload", name="中文说明.txt", purpose="attachment"):
        return browser.post("/api/v1/files", files={"file": (name, b"same content", "text/plain")}, data={"purpose": purpose, "projectId": project}, headers={"X-CSRF-Token": token, "Idempotency-Key": key})
    assert upload(other, other_csrf).status_code == 403
    assert upload(other, other_csrf, purpose="template").status_code == 403
    stored = upload(client, csrf)
    assert stored.status_code == 201, stored.text
    assert upload(client, csrf).json()["data"]["id"] == stored.json()["data"]["id"]
    assert upload(client, csrf, second["id"]).status_code == 409
    assert upload(client, csrf, name="different.txt").status_code == 409
    file_id = stored.json()["data"]["id"]
    assert other.get(f"/api/v1/files/{file_id}/content").status_code == 404
    downloaded = client.get(f"/api/v1/files/{file_id}/content")
    assert downloaded.status_code == 200 and downloaded.content == b"same content"
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    with connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()
    assert count["total"] == 1


def test_parallel_upload_reuses_one_record(client):
    owner = _user("core-owner", ["project_creator"])
    csrf = _login(client, "core-owner")
    project = _project(client, csrf, owner)
    other = TestClient(client.app)
    other_csrf = _login(other, "core-owner")
    def upload(browser, token):
        return browser.post("/api/v1/files", files={"file": ("note.txt", b"same content", "text/plain")}, data={"purpose": "attachment", "projectId": project["id"]}, headers={"X-CSRF-Token": token, "Idempotency-Key": "concurrent-upload"})
    results = _parallel(lambda: upload(client, csrf), lambda: upload(other, other_csrf))
    assert [result.status_code for result in results] == [201, 201], [result.text for result in results]
    assert results[0].json()["data"]["id"] == results[1].json()["data"]["id"]
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()["total"] == 1


def test_failed_upload_commit_removes_object_and_record(client, monkeypatch):
    owner = _user("core-owner", ["project_creator"])
    csrf = _login(client, "core-owner")
    project = _project(client, csrf, owner)
    execute = DictConnection.execute
    def fail_idempotency(connection, sql, params=()):
        if "INSERT INTO idempotency_keys (user_id, idempotency_key, request_hash, file_id)" in sql:
            raise RuntimeError("isolated injected storage failure")
        return execute(connection, sql, params)
    monkeypatch.setattr(DictConnection, "execute", fail_idempotency)
    response = client.post("/api/v1/files", files={"file": ("note.txt", b"rollback payload", "text/plain")}, data={"purpose": "attachment", "projectId": project["id"]}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "failed-upload"})
    assert response.status_code == 500
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()["total"] == 0
    assert not list((get_settings().file_data_dir / "objects").iterdir())


@pytest.mark.parametrize("resource", ["users", "projects"])
def test_parallel_creation_with_one_idempotency_key_reuses_one_record(client, monkeypatch, resource):
    owner = _user("core-admin")
    csrf = _login(client, "core-admin")
    other = TestClient(client.app)
    other_csrf = _login(other, "core-admin")
    execute = DictConnection.execute
    def slow_read(connection, sql, params=()):
        result = execute(connection, sql, params)
        if "from idempotency_keys" in sql:
            time.sleep(.05)
        return result
    monkeypatch.setattr(DictConnection, "execute", slow_read)
    payload = {"name": "并发创建", "ownerId": str(owner), "status": "not_started"} if resource == "projects" else {"account": "parallel-user", "name": "并发创建", "password": PASSWORD, "globalRoles": ["project_creator"]}
    url = f"/api/v1/{resource}"
    results = _parallel(
        lambda: client.post(url, json=payload, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "same-create"}),
        lambda: other.post(url, json=payload, headers={"X-CSRF-Token": other_csrf, "Idempotency-Key": "same-create"}),
    )
    assert [result.status_code for result in results] == [201, 201], [result.text for result in results]
    assert results[0].json()["data"]["id"] == results[1].json()["data"]["id"]
    with connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) AS total FROM {resource} WHERE name = '并发创建'").fetchone()["total"] == 1


def test_failed_create_user_does_not_leave_account_without_audit_or_password(client, monkeypatch):
    _user("core-admin")
    csrf = _login(client, "core-admin")
    def fail_record(*_args):
        raise RuntimeError("isolated audit failure")
    monkeypatch.setattr("app.users._record", fail_record)
    response = client.post("/api/v1/users", json={"account": "failed-user", "name": "失败账号", "password": PASSWORD, "globalRoles": ["auditor"]}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "failed-create"})
    assert response.status_code == 500
    with connect() as connection:
        assert connection.execute("SELECT id FROM users WHERE account_key = 'failed-user'").fetchone() is None
        assert connection.execute("SELECT file_id FROM idempotency_keys WHERE idempotency_key = 'failed-create'").fetchone() is None


def test_failed_password_reset_preserves_original_credentials_and_session(client, monkeypatch):
    _user("core-admin")
    target = _user("member", ["project_creator"])
    csrf = _login(client, "core-admin")
    member = TestClient(client.app)
    _login(member, "member")
    def fail_record(*_args):
        raise RuntimeError("isolated audit failure")
    monkeypatch.setattr("app.users._record", fail_record)
    response = client.post(f"/api/v1/users/{target}/reset-credentials", json={"expectedRevision": 0, "reason": "测试回滚", "password": NEW_PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 500
    assert member.get("/api/v1/auth/me").status_code == 200
    with connect() as connection:
        current = connection.execute("SELECT password_hash, revision FROM users WHERE id = %s", (target,)).fetchone()
    assert current["revision"] == 0 and verify_password(PASSWORD, current["password_hash"])


def test_disabling_user_rechecks_active_jobs_without_changing_completed_jobs(client):
    _user("core-admin")
    target = _user("member", ["project_creator"])
    csrf = _login(client, "core-admin")
    queued, complete = uuid4(), uuid4()
    with connect() as connection:
        with connection.transaction():
            for job_id, status in [(queued, "queued"), (complete, "succeeded")]:
                connection.execute("INSERT INTO jobs (id, created_by, kind, status, target_kind, target_name) VALUES (%s, %s, 'document', %s, 'document', '测试')", (job_id, target, status))
    response = client.post(f"/api/v1/users/{target}/disable", json={"expectedRevision": 0, "reason": "停用"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    with connect() as connection:
        assert connection.execute("SELECT status FROM jobs WHERE id = %s", (queued,)).fetchone()["status"] == "needs_verification"
        assert connection.execute("SELECT status FROM jobs WHERE id = %s", (complete,)).fetchone()["status"] == "succeeded"


def test_knowledge_reader_cannot_upload_into_read_only_base(client):
    owner = _user("knowledge-owner", ["knowledge_manager"])
    reader = _user("knowledge-reader", ["knowledge_manager"])
    csrf = _login(client, "knowledge-owner")
    response = client.post("/api/v1/knowledge-bases", json={"name": "私有资料", "summary": "", "scope": "organization", "ownerId": str(owner)}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    base = response.json()["data"]
    grant = client.put(f"/api/v1/knowledge-bases/{base['id']}/access", json={"expectedRevision": base["revision"], "readers": [str(reader)], "maintainers": []}, headers={"X-CSRF-Token": csrf})
    assert grant.status_code == 200, grant.text
    other = TestClient(client.app)
    reader_csrf = _login(other, "knowledge-reader")
    upload = other.post("/api/v1/files", files={"file": ("note.txt", b"reader upload", "text/plain")}, data={"purpose": "source", "baseId": base["id"]}, headers={"X-CSRF-Token": reader_csrf, "Idempotency-Key": "reader-upload"})
    assert upload.status_code == 403, upload.text
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM files").fetchone()["total"] == 0


def test_archive_checks_current_queued_jobs_and_preserves_completed(client):
    owner = _user("core-admin")
    csrf = _login(client, "core-admin")
    project = _project(client, csrf, owner)
    job_id = uuid4()
    with connect() as connection:
        with connection.transaction():
            connection.execute("INSERT INTO jobs (id, project_id, created_by, kind, status, target_kind, target_name) VALUES (%s, %s, %s, 'document', 'queued', 'document', '测试')", (job_id, project["id"], owner))
    url = f"/api/v1/projects/{project['id']}/archive"
    assert client.post(url, json={"expectedRevision": 0, "reason": "归档"}, headers={"X-CSRF-Token": csrf}).status_code == 409
    with connect() as connection:
        with connection.transaction():
            connection.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (job_id,))
    assert client.post(url, json={"expectedRevision": 0, "reason": "归档"}, headers={"X-CSRF-Token": csrf}).status_code == 200
