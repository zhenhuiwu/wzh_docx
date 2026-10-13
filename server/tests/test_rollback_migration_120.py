"""迁移 120 单独回滚：有数据默认拒绝，--force 后导出、备份、删表，再迁移可重建。"""

import sqlite3


def test_rollback_120(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate
    from app.rollback_migration_120 import TABLES, rollback

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=off")
            connection.execute("insert into project_tasks(id,project_id,title) values ('t','p','任务')")
        assert rollback(path, apply=False) == 0
        assert rollback(path, apply=True) == 3
        assert rollback(path, apply=True, force=True) == 0
        with sqlite3.connect(path) as connection:
            names = {r[0] for r in connection.execute("select name from sqlite_master where type='table'")}
        assert not names & set(TABLES)
        assert list(tmp_path.glob("app.db.project_tasks-120-*.csv"))
        assert rollback(path, apply=True) == 0 and "不需要回滚" in capsys.readouterr().out
        assert migrate() == ["120_tasks_templates.sql"]
    finally:
        config.get_settings.cache_clear()
