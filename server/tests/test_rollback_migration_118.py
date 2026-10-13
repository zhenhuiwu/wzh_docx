import sqlite3
from uuid import uuid4

from app.db import connect, database_path, migrate
from app.identity import create_user
from app.rollback_migration_118 import VERSION, rollback


def test_rollback_118_empty_database_can_reapply():
    migrate()
    path = database_path()
    assert rollback(path, apply=False) == 0
    assert rollback(path, apply=True) == 0
    assert rollback(path, apply=True) == 0
    assert migrate() == ["118_document_assignments.sql"]


def test_rollback_118_requires_force_and_backup_preserves_protection():
    migrate()
    path = database_path()
    user = create_user(account="rollback-118", name="分工回滚", password="rollback-test-password", roles=["system_admin"])
    project, document = uuid4(), uuid4()
    with connect() as connection:
        connection.execute("INSERT INTO projects(id,name,owner_id) VALUES (%s,'回滚项目',%s)", (project, user))
        connection.execute("INSERT INTO documents(id,project_id,name,owner_id,assignments_enabled) VALUES (%s,%s,'回滚文档',%s,1)",
                           (document, project, user))
        connection.commit()
    assert rollback(path, apply=True) == 3
    assert not list(path.parent.glob(f"{path.name}.before-rollback-118-*.db"))
    assert rollback(path, apply=True, force=True) == 0
    [backup] = list(path.parent.glob(f"{path.name}.before-rollback-118-*.db"))
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT assignments_enabled FROM documents WHERE id=?", (str(document),)).fetchone()[0] == 1
    with sqlite3.connect(path) as connection:
        assert "assignments_enabled" not in {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (VERSION,)).fetchone()


def test_rollback_118_refuses_running_service_and_newer_migration(monkeypatch):
    migrate()
    path = database_path()
    monkeypatch.setattr("app.rollback_migration_118._in_use", lambda *_args: "服务锁")
    assert rollback(path, apply=True, force=True) == 2
    monkeypatch.setattr("app.rollback_migration_118._in_use", lambda *_args: None)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO schema_migrations(version) VALUES ('119_future')")
    assert rollback(path, apply=True, force=True) == 2
    assert not list(path.parent.glob(f"{path.name}.before-rollback-118-*.db"))


def test_older_rollback_detects_assignments_even_without_migration_record():
    from app.rollback_migration_117 import rollback as rollback_117
    migrate()
    path = database_path()
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=?", (VERSION,))
    assert rollback_117(path, apply=True, force=True) == 2
    assert rollback(path, apply=True) == 0
    assert rollback_117(path, apply=True, force=True) == 0
