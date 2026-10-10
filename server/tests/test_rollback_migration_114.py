"""三审：迁移 114 可单独回滚，回滚后重新迁移能再加回来。"""

import sqlite3

from app.rollback_migration_114 import COLUMN, VERSION, rollback


def _columns(path):
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute("pragma table_info(document_objects)")}


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_114_dry_run_then_apply_then_reapply(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        assert COLUMN in _columns(path) and _recorded(path) == 1
        assert rollback(path, apply=False) == 0
        assert COLUMN in _columns(path) and _recorded(path) == 1  # 只检查不改库
        assert rollback(path, apply=True) == 0
        assert COLUMN not in _columns(path) and _recorded(path) == 0
        assert list(tmp_path.glob("app.db.before-rollback-114-*.db"))
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        migrate()
        assert COLUMN in _columns(path) and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()
