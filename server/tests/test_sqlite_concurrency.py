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
