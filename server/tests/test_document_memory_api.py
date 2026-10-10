"""The memory inspector can page through exact accepted requests with document access checks."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import clear_settings_cache
from app.db import connect, migrate
from app.document_memory import record_memory
from app.identity import create_user
from app.main import create_app


def login(client, account):
    challenge = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    result = client.post("/api/v1/auth/login", json={"account": account, "password": "memory-api-password"},
                         headers={"X-CSRF-Token": challenge})
    assert result.status_code == 200, result.text


def test_memory_api_pages_all_original_requests_and_keeps_document_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'memory-api.db'}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    clear_settings_cache()
    try:
        migrate()
        owner = create_user(account="memory-api-owner", name="记忆测试", password="memory-api-password", roles=["project_creator"])
        create_user(account="memory-api-other", name="另一用户", password="memory-api-password", roles=["project_creator"])
        project, document = uuid4(), uuid4()
        with connect() as connection:
            connection.execute("INSERT INTO projects (id, name, owner_id) VALUES (%s, %s, %s)", (project, "中性项目", owner))
            connection.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (%s, %s, 'owner')", (project, owner))
            connection.execute("INSERT INTO documents (id, project_id, name, owner_id) VALUES (%s, %s, %s, %s)",
                               (document, project, "中性文档", owner))
            for revision in range(1, 24):
                record_memory(connection, document, f"原始要求 {revision}：" + "完整细节。" * 20, "section", "范围", revision)
            connection.commit()
        url = f"/api/v1/documents/{document}/memory"
        with TestClient(create_app()) as client:
            assert client.get(url).status_code == 401
            login(client, "memory-api-owner")
            default = client.get(url).json()["data"]
            assert len(default["items"]) == 5 and default["offset"] == 0 and default["hasMore"]
            assert default["summary"]
            pages = [client.get(url, params={"offset": offset, "limit": 10}).json()["data"] for offset in (0, 10, 20)]
            assert [len(page["items"]) for page in pages] == [10, 10, 3]
            assert [page["hasMore"] for page in pages] == [True, True, False]
            original = [item["instruction"] for page in pages for item in page["items"]]
            assert len(set(original)) == 23 and original[-1] == "原始要求 1：" + "完整细节。" * 20
            assert client.get(url, params={"offset": 30, "limit": 10}).json()["data"]["items"] == []
            for params in ({"offset": -1}, {"limit": 0}, {"limit": 11}):
                assert client.get(url, params=params).status_code == 422
            login(client, "memory-api-other")
            assert client.get(url, params={"offset": 10, "limit": 10}).status_code == 404
    finally:
        clear_settings_cache()
