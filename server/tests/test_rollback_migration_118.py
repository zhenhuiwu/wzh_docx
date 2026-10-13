"""A07：迁移 118 可单独回滚（有分工时需 --force，先导出 CSV、整库备份），回滚后结构与 117 状态一致；
有更新迁移时拒绝；117 在 118 还在时拒绝；重新迁移能再建回来。"""

import sqlite3

from tests.migration_chain import files_after, rollback_newer_than

from app.rollback_migration_118 import VERSION, rollback
from tests.test_document_objects_migration import _migrate_until
from tests.test_rollback_migration_116 import _schema


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_118_restores_117_schema(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate
    from app.rollback_migration_117 import rollback as rollback_117

    reference = tmp_path / "ref"
    reference.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{reference / 'app.db'}")
    config.get_settings.cache_clear()
    _migrate_until(reference, monkeypatch, 117)
    expected = _schema(reference / "app.db")

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        assert rollback(path, apply=True, force=True) == 2       # 更新的迁移还在
        assert rollback_newer_than(path, 118) == 0
        capsys.readouterr()
        assert rollback_117(path, apply=True, force=True) == 2   # 118 还在
        assert "rollback_migration_118" in capsys.readouterr().err
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=off")
            connection.execute("insert into document_object_assignments(document_id, object_id, assignee_id) values ('d1','@document','u1')")
        assert rollback(path, apply=False) == 0
        assert rollback(path, apply=True) == 3   # 有分工：默认拒绝
        assert _recorded(path) == 1
        assert rollback(path, apply=True, force=True) == 0
        assert _recorded(path) == 0
        assert _schema(path) == expected
        assert list(tmp_path.glob("app.db.document_object_assignments-118-*.csv"))
        assert list(tmp_path.glob("app.db.before-rollback-118-*.db"))
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        assert migrate() == files_after(117)
        with sqlite3.connect(path) as connection:
            assert connection.execute("select count(*) from document_object_assignments").fetchone()[0] == 0
    finally:
        config.get_settings.cache_clear()


def test_rollback_118_refuses_when_later_migration_recorded(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        with sqlite3.connect(path) as connection:
            connection.execute("insert into schema_migrations(version) values ('119_future')")
        assert rollback(path, apply=True, force=True) == 2
        assert "119_future" in capsys.readouterr().err
        assert _recorded(path) == 1
        assert not list(tmp_path.glob("app.db.before-rollback-118-*.db"))
    finally:
        config.get_settings.cache_clear()


def test_existing_documents_open_new_documents_enforced(tmp_path, monkeypatch):
    """U-14：迁移时已有文档设为 open；列默认 open（新建接口显式写 enforced，见 test_assignments_a07）。"""
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        _migrate_until(tmp_path, monkeypatch, 117)
        with sqlite3.connect(path) as connection:
            columns = [row[1] for row in connection.execute("pragma table_info(documents)")]
            assert "assignment_mode" not in columns
            connection.execute("pragma foreign_keys=off")
            connection.execute("insert into documents(id, project_id, name, source_type, owner_id) values ('old','p','旧文档','blank','u')")
        assert migrate() == files_after(117)
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=off")
            connection.execute("insert into documents(id, project_id, name, source_type, owner_id) values ('new','p','新文档','blank','u')")
            modes = dict(connection.execute("select id, assignment_mode from documents"))
        assert modes == {"old": "open", "new": "open"}
    finally:
        config.get_settings.cache_clear()
