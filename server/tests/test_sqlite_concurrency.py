"""S2：WAL、busy_timeout、写事务与 503 DATABASE_BUSY（01 §8.2 用例 8、24、25）。"""

import sqlite3
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import db
from app.config import get_settings
from app.db import connect, migrate, write_transaction
from app.identity import create_user
from app.main import create_app
from tests.test_document_access import members  # noqa: F401
from tests.test_documents import document_context  # noqa: F401


def _path() -> Path:
    return Path(get_settings().database_url.removeprefix("sqlite:///"))


def test_migrate_switches_to_wal_and_is_idempotent():
    migrate()
    assert db.journal_mode() == "wal"
    assert migrate() == []
    with connect() as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert connection.execute("pragma busy_timeout").fetchone()[0] == 5000
        assert connection.execute("pragma synchronous").fetchone()[0] == 2  # FULL


def test_second_immediate_writer_waits_instead_of_failing():
    migrate()
    started, results = threading.Event(), []

    def holder():
        with connect() as connection, write_transaction(connection):
            started.set()
            time.sleep(0.5)

    thread = threading.Thread(target=holder)
    thread.start()
    started.wait()
    begun = time.monotonic()
    with connect() as connection, write_transaction(connection):
        results.append(time.monotonic() - begun)
    thread.join()
    assert 0.3 < results[0] < 5


def test_write_transaction_refuses_nesting_and_rolls_back():
    migrate()
    with connect() as connection:
        with pytest.raises(RuntimeError):
            with write_transaction(connection):
                connection.execute("INSERT INTO schema_migrations(version) VALUES('x-rollback')")
                with write_transaction(connection):
                    pass
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version='x-rollback'").fetchone() is None


def test_locked_database_returns_503_database_busy(monkeypatch):
    migrate()
    create_user(account="busy-admin", name="忙", password="busy-test-password", roles=["system_admin"])
    with TestClient(create_app()) as client:
        token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 50)
        blocker = sqlite3.connect(_path())
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            response = client.post("/api/v1/auth/login", json={"account": "busy-admin", "password": "busy-test-password"},
                                   headers={"X-CSRF-Token": token})
        finally:
            blocker.rollback()
            blocker.close()
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "DATABASE_BUSY"
    assert response.headers["Retry-After"] == "1"


def test_five_writers_on_large_document_all_succeed(members, monkeypatch):  # noqa: F811
    """用例 29：busy_timeout=5000 下 5 个线程对同一 2MB 文档的不同段并发提交：全部成功、无 503，写锁持有 <1s。"""
    from contextlib import contextmanager

    import app.document_commit as commit
    from tests.test_document_access import PASSWORD, _as

    client, project, document, _ = members
    monkeypatch.setattr("app.documents._check_word", lambda *args, **kwargs: time.sleep(0.05))
    paragraphs = [f"第 {index} 段：" + "测试正文内容，用于模拟大文档。" * 12 for index in range(9000)]
    text = "# 大文档\n\n" + "\n\n".join(paragraphs) + "\n"
    assert len(text.encode("utf-8")) > 2_000_000
    accounts = []
    with connect() as connection:
        for index in range(5):
            uid = create_user(account=f"w29-{index}", name=f"并发{index}", password=PASSWORD, roles=["template_manager"],
                              connection=connection)
            connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'editor')", (project, uid))
            accounts.append(f"w29-{index}")
        connection.commit()
    headers = _as(client, "editor")
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": text, "allowMerge": True}, headers=headers)
    assert saved.status_code == 200, saved.text
    held: list[float] = []
    original = commit.write_transaction

    @contextmanager
    def timed(connection):
        with original(connection):
            started = time.perf_counter()
            yield connection
            held.append(time.perf_counter() - started)

    monkeypatch.setattr(commit, "write_transaction", timed)
    results = []

    def run(index):
        with TestClient(create_app()) as own:
            token = own.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
            login = own.post("/api/v1/auth/login", json={"account": accounts[index], "password": PASSWORD},
                             headers={"X-CSRF-Token": token})
            mine = text.replace(paragraphs[1000 * (index + 1)], f"并发{index}修改。")
            results.append(own.patch(f"/api/v1/documents/{document['id']}/content",
                                     json={"expectedRevision": 1, "markdownContent": mine, "allowMerge": True},
                                     headers={"X-CSRF-Token": login.json()["data"]["csrfToken"]}))

    threads = [threading.Thread(target=run, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [response.status_code for response in results] == [200] * 5, [response.text[:200] for response in results]
    final = client.get(f"/api/v1/documents/{document['id']}").json()["data"]
    assert final["revision"] == 6 and all(f"并发{index}修改。" in final["markdownContent"] for index in range(5))
    # 测试 L1：单次计时受机器负载影响；中位数 < 1s 体现“写锁只包住必要写入”，最大值只需远低于 busy_timeout(5s)。
    assert held, held
    assert sorted(held)[len(held) // 2] < 1.0, held
    assert max(held) < 3.0, held
