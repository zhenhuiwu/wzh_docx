"""方案 C：迁移 115 可单独回滚，回滚后重新迁移能再建回来。"""

import sqlite3

from app.rollback_migration_115 import TABLE, VERSION, rollback


def _has_table(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from sqlite_master where type='table' and name=?", (TABLE,)).fetchone()[0]


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_115_dry_run_then_apply_then_reapply(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        assert _has_table(path) == 1 and _recorded(path) == 1
        assert rollback(path, apply=False) == 0
        assert _has_table(path) == 1 and _recorded(path) == 1
        assert rollback(path, apply=True) == 0
        assert _has_table(path) == 0 and _recorded(path) == 0
        assert list(tmp_path.glob("app.db.before-rollback-115-*.db"))
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        assert migrate() == ["115_document_conflict_mine_snapshots.sql"]
        assert _has_table(path) == 1 and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()
