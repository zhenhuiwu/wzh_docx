"""迁移 122 单独回滚：有“已核对”记录默认拒绝，--force 后备份并删列，再迁移可重建。"""

import sqlite3

from tests.migration_chain import files_from


def test_rollback_122(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate
    from app.rollback_migration_122 import rollback

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=off")
            connection.execute("insert into document_upstreams(document_id,upstream_document_id,acked_baseline_seq) values ('a','b',2)")
        assert rollback(path, apply=False) == 0
        assert rollback(path, apply=True) == 3
        assert rollback(path, apply=True, force=True) == 0
        with sqlite3.connect(path) as connection:
            cols = {r[1] for r in connection.execute("pragma table_info(document_upstreams)")}
        assert "acked_baseline_seq" not in cols and list(tmp_path.glob("app.db.before-rollback-122-*.db"))
        assert rollback(path, apply=True) == 0 and "不需要回滚" in capsys.readouterr().out
        assert migrate() == files_from(122)[:1]
    finally:
        config.get_settings.cache_clear()
