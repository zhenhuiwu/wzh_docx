"""Editable skill rules persist without replacing builtin edits or losing CAS."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import clear_settings_cache
from app.db import DictConnection, connect, migrate
from app.errors import AppError
from app.identity import create_user
from app.writing_skills import BUILTINS, load_skill_snapshot, seed_writing_skills, validate_skill_snapshot

PASSWORD = "correct-horse-1"


@pytest.fixture()
def client():
    from app.main import create_app

    clear_settings_cache()
    migrate()
    with TestClient(create_app()) as browser:
        yield browser
    clear_settings_cache()


def _login(client, account="skill-admin", role="system_admin", *, create=True):
    if create:
        create_user(account=account, name=account, password=PASSWORD, roles=[role])
    csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post("/api/v1/auth/login", json={"account": account, "password": PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    return response.json()["data"]["csrfToken"]


def _create(client, csrf, name="格式要求", **values):
    response = client.post("/api/v1/writing-skills", json={"name": name, "instructions": "保留现有标题结构，明确说明未知内容。", **values}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _parallel(*calls):
    barrier = Barrier(len(calls))
    def run(call):
        barrier.wait(timeout=5)
        return call()
    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return list(executor.map(run, calls))


def test_seed_has_three_normative_skills_and_preserves_edits_renames_disabled_status(client):
    csrf = _login(client)
    initial = client.get("/api/v1/writing-skills").json()["data"]
    assert initial["total"] == 3
    assert {item["baseKind"] for item in initial["items"]} == {"general", "srs", "sdd"}
    assert all(item["builtin"] and item["instructions"] and item["revision"] == 0 for item in initial["items"])
    for item in initial["items"]:
        assert "本说明描述订单" not in item["instructions"]
        assert "XXXXXXXX公司" not in item["instructions"]
    target = next(item for item in initial["items"] if item["baseKind"] == "srs")
    response = client.patch(f"/api/v1/writing-skills/{target['id']}", json={"expectedRevision": 0, "name": "自用需求写法", "instructions": "仅依据本项目确认的需求编写。", "status": "disabled", "forbiddenTerms": ["大幅提升"], "maxChars": 1000}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    edited = response.json()["data"]
    for _ in range(2):
        seed_writing_skills()
    from app.main import create_app
    with TestClient(create_app()):
        pass
    preserved = client.get(f"/api/v1/writing-skills/{target['id']}").json()["data"]
    assert preserved == edited
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 3
    assert load_skill_snapshot(target["id"], require_active=False) == edited
    with pytest.raises(AppError) as disabled:
        load_skill_snapshot(target["id"])
    assert disabled.value.code == "SKILL_DISABLED"


def test_builtin_source_kind_immutable_but_custom_can_change(client):
    csrf = _login(client)
    builtin_id = BUILTINS[1]["id"]
    failed = client.patch(f"/api/v1/writing-skills/{builtin_id}", json={"expectedRevision": 0, "baseKind": "sdd"}, headers={"X-CSRF-Token": csrf})
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "IMMUTABLE_FIELD"
    assert client.get(f"/api/v1/writing-skills/{builtin_id}").json()["data"]["baseKind"] == "srs"
    custom = _create(client, csrf, maxChars=1000)
    updated = client.patch(f"/api/v1/writing-skills/{custom['id']}", json={"expectedRevision": 0, "baseKind": "sdd", "maxChars": None}, headers={"X-CSRF-Token": csrf})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["baseKind"] == "sdd" and updated.json()["data"]["maxChars"] is None
    assert not updated.json()["data"]["builtin"]
    assert client.delete(f"/api/v1/writing-skills/{custom['id']}").status_code == 405


def test_only_system_admin_writes_all_logged_in_users_read(client):
    assert client.get("/api/v1/writing-skills").status_code == 401
    assert client.get(f"/api/v1/writing-skills/{BUILTINS[0]['id']}").status_code == 401
    reader_csrf = _login(client, "skill-reader", "template_manager")
    assert client.get("/api/v1/writing-skills").status_code == 200
    assert client.get(f"/api/v1/writing-skills/{BUILTINS[0]['id']}").status_code == 200
    assert client.post("/api/v1/writing-skills", json={"name": "越权技能", "instructions": "规则"}, headers={"X-CSRF-Token": reader_csrf}).status_code == 403
    assert client.patch(f"/api/v1/writing-skills/{BUILTINS[0]['id']}", json={"expectedRevision": 0, "status": "disabled"}, headers={"X-CSRF-Token": reader_csrf}).status_code == 403
    admin_csrf = _login(client, "skill-admin")
    del admin_csrf
    assert client.post("/api/v1/writing-skills", json={"name": "缺少CSRF", "instructions": "规则"}).status_code == 403
    assert client.patch(f"/api/v1/writing-skills/{BUILTINS[0]['id']}", json={"expectedRevision": 0, "status": "disabled"}, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 3


def test_list_pages_literal_name_filter_and_status(client):
    csrf = _login(client)
    first = _create(client, csrf, "自用_%", status="disabled")
    _create(client, csrf, "自用一")
    _create(client, csrf, "自用二")
    page1 = client.get("/api/v1/writing-skills", params={"page": 1, "pageSize": 2, "name": "自用"}).json()["data"]
    page2 = client.get("/api/v1/writing-skills", params={"page": 2, "pageSize": 2, "name": "自用"}).json()["data"]
    assert page1["total"] == page2["total"] == 3
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert not {row["id"] for row in page1["items"]} & {row["id"] for row in page2["items"]}
    literal = client.get("/api/v1/writing-skills", params={"name": "_%", "status": "disabled"}).json()["data"]
    assert literal["total"] == 1 and literal["items"][0]["id"] == first["id"]
    assert client.get("/api/v1/writing-skills", params={"status": "active"}).json()["data"]["total"] == 5
    empty = client.get("/api/v1/writing-skills", params={"name": "不存在"}).json()["data"]
    assert empty["total"] == 0 and empty["items"] == []


def test_same_second_creates_and_update_are_listed_in_actual_write_order(client, monkeypatch):
    csrf = _login(client)
    timestamps = iter(("2099-01-01 00:00:00.100000", "2099-01-01 00:00:00.200000", "2099-01-01 00:00:00.300000"))
    monkeypatch.setattr("app.writing_skills._timestamp", lambda: next(timestamps))
    first = _create(client, csrf, "A earlier")
    second = _create(client, csrf, "Z later")
    listed = client.get("/api/v1/writing-skills", params={"pageSize": 1}).json()["data"]
    assert listed["items"][0]["id"] == second["id"]
    changed = client.patch(f"/api/v1/writing-skills/{first['id']}", json={"expectedRevision": 0, "instructions": "最近修改。"}, headers={"X-CSRF-Token": csrf})
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/writing-skills", params={"pageSize": 1}).json()["data"]["items"][0]["id"] == first["id"]
    with connect() as connection:
        stored = connection.execute("SELECT updated_at FROM writing_skills WHERE id=%s", (first["id"],)).fetchone()["updated_at"]
    assert stored.microsecond == 300000


@pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"page": "bad"}, {"pageSize": 0}, {"pageSize": 51}, {"status": "retired"}])
def test_invalid_list_query_is_422(client, params):
    _login(client)
    assert client.get("/api/v1/writing-skills", params=params).status_code == 422


@pytest.mark.parametrize("changes", [
    {"name": "  "}, {"name": "a" * 121}, {"description": "a" * 501},
    {"instructions": " "}, {"instructions": "a" * 12001}, {"baseKind": "other"},
    {"forbiddenTerms": ["a"] * 31}, {"forbiddenTerms": ["a" * 81]},
    {"forbiddenTerms": [" "]}, {"maxChars": 99}, {"maxChars": 20001},
    {"maxChars": "1000"}, {"status": "retired"}, {"builtin": True},
])
def test_create_validation_preserves_all_records(client, changes):
    csrf = _login(client)
    response = client.post("/api/v1/writing-skills", json={"name": "测试规则", "instructions": "有效规则", **changes}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 3


def test_casefold_duplicate_creation_and_rename_are_conflicts(client):
    csrf = _login(client)
    first = _create(client, csrf, " Straße ")
    assert first["name"] == "Straße"
    duplicate = client.post("/api/v1/writing-skills", json={"name": "STRASSE", "instructions": "规则"}, headers={"X-CSRF-Token": csrf})
    assert duplicate.status_code == 409, duplicate.text
    other = _create(client, csrf, "独立规范")
    rename = client.patch(f"/api/v1/writing-skills/{other['id']}", json={"expectedRevision": 0, "name": "strasse"}, headers={"X-CSRF-Token": csrf})
    assert rename.status_code == 409
    assert client.get(f"/api/v1/writing-skills/{other['id']}").json()["data"] == other


def test_parallel_cas_accepts_only_one_save(client, monkeypatch):
    csrf = _login(client)
    current = _create(client, csrf)
    other = TestClient(client.app)
    other_csrf = _login(other, create=False)
    execute = DictConnection.execute
    def slow_read(connection, sql, params=()):
        cursor = execute(connection, sql, params)
        if "SELECT * FROM writing_skills WHERE id" in sql:
            time.sleep(.05)
        return cursor
    monkeypatch.setattr(DictConnection, "execute", slow_read)
    url = f"/api/v1/writing-skills/{current['id']}"
    results = _parallel(
        lambda: client.patch(url, json={"expectedRevision": 0, "instructions": "并发要求甲"}, headers={"X-CSRF-Token": csrf}),
        lambda: other.patch(url, json={"expectedRevision": 0, "instructions": "并发要求乙"}, headers={"X-CSRF-Token": other_csrf}),
    )
    assert sorted(result.status_code for result in results) == [200, 409], [result.text for result in results]
    winning = next(result.json()["data"] for result in results if result.status_code == 200)
    assert client.get(url).json()["data"] == winning
    assert winning["revision"] == 1
    conflict = next(result for result in results if result.status_code == 409)
    assert conflict.json()["error"]["currentRevision"] == 1


def test_optional_create_idempotency_persists_and_rejects_changes(client):
    csrf = _login(client)
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "stable-skill"}
    payload = {"name": "稳定规则", "instructions": "统一术语", "forbiddenTerms": [" 广泛提升 ", "广泛提升"]}
    initial = client.post("/api/v1/writing-skills", json=payload, headers=headers)
    assert initial.status_code == 201, initial.text
    assert initial.json()["data"]["forbiddenTerms"] == ["广泛提升"]
    from app.main import create_app
    with TestClient(create_app()):
        pass
    repeated = client.post("/api/v1/writing-skills", json=payload, headers=headers)
    assert repeated.status_code == 201 and repeated.json()["data"] == initial.json()["data"]
    conflict = client.post("/api/v1/writing-skills", json={**payload, "instructions": "另一套规则"}, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.post("/api/v1/writing-skills", json={"name": "新规则", "instructions": "规则"}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "x" * 129}).status_code == 422
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 4


def test_parallel_same_create_key_reuses_one_skill(client):
    csrf = _login(client)
    other = TestClient(client.app)
    other_csrf = _login(other, create=False)
    payload = {"name": "并发创建", "instructions": "统一格式"}
    def call(browser, token):
        return browser.post("/api/v1/writing-skills", json=payload, headers={"X-CSRF-Token": token, "Idempotency-Key": "parallel-create"})
    results = _parallel(lambda: call(client, csrf), lambda: call(other, other_csrf))
    assert [result.status_code for result in results] == [201, 201], [result.text for result in results]
    assert results[0].json()["data"]["id"] == results[1].json()["data"]["id"]
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 4


def test_failed_create_key_save_rolls_back_new_skill(client, monkeypatch):
    csrf = _login(client)
    execute = DictConnection.execute
    def fail_key(connection, sql, params=()):
        if "INSERT INTO writing_skill_requests" in sql:
            raise RuntimeError("isolated idempotency failure")
        return execute(connection, sql, params)
    monkeypatch.setattr(DictConnection, "execute", fail_key)
    response = client.post("/api/v1/writing-skills", json={"name": "失败规则", "instructions": "规则"}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "failed-key"})
    assert response.status_code == 500
    assert client.get("/api/v1/writing-skills").json()["data"]["total"] == 3
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM writing_skill_requests").fetchone()["total"] == 0


def test_invalid_patch_does_not_change_values_and_missing_ids_are_clear(client):
    csrf = _login(client)
    original = _create(client, csrf)
    url = f"/api/v1/writing-skills/{original['id']}"
    for changes in [{}, {"instructions": None}, {"instructions": " "}, {"name": None}, {"description": "x" * 501}, {"forbiddenTerms": ["x" * 81]}, {"builtin": False}]:
        result = client.patch(url, json={"expectedRevision": 0, **changes}, headers={"X-CSRF-Token": csrf})
        assert result.status_code == 422, result.text
        assert client.get(url).json()["data"] == original
    assert client.get("/api/v1/writing-skills/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/writing-skills/{uuid4()}").status_code == 404


def test_snapshot_validation_is_pure_detached_and_preserves_source_revision(client, monkeypatch):
    csrf = _login(client)
    original = _create(client, csrf, forbiddenTerms=["笼统表述"], maxChars=1000)
    def no_io():
        raise AssertionError("pure snapshot validation must not open database")
    monkeypatch.setattr("app.writing_skills.connect", no_io)
    validated = validate_skill_snapshot(original)
    assert validated == original
    validated["forbiddenTerms"].append("另一个词")
    assert original["forbiddenTerms"] == ["笼统表述"]
    with pytest.raises(AppError) as invalid:
        validate_skill_snapshot({**original, "instructions": ""})
    assert invalid.value.status_code == 422
