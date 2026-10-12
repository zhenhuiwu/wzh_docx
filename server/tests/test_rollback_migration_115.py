"""方案 C：迁移 115 可单独回滚，回滚后重新迁移能再建回来。"""

import sqlite3

from app.rollback_migration_115 import TABLE, VERSION, rollback
from app.rollback_migration_116 import rollback as rollback_116
from app.rollback_migration_117 import rollback as rollback_117


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
        assert rollback_117(path, apply=True) == 0 and rollback_116(path, apply=True) == 0  # 倒序：先回滚 116
        assert _has_table(path) == 1 and _recorded(path) == 1
        assert rollback(path, apply=False) == 0
        assert _has_table(path) == 1 and _recorded(path) == 1
        assert rollback(path, apply=True) == 0
        assert _has_table(path) == 0 and _recorded(path) == 0
        assert list(tmp_path.glob("app.db.before-rollback-115-*.db"))
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        assert migrate() == ["115_document_conflict_mine_snapshots.sql", "116_project_members_v2.sql", "117_template_versions.sql"]
        assert _has_table(path) == 1 and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()


def _versions(path):
    with sqlite3.connect(path) as connection:
        return sorted(row[0] for row in connection.execute("select version from schema_migrations"))


def test_rollback_115_refuses_while_116_present(tmp_path, monkeypatch, capsys):
    """测试 20 M-1（qa20 mig116.sh C3）：116 还在时回滚 115 必须拒绝（退出码 2、说明先回滚 116），库不变、不留备份。
    只检查模式同样拒绝。116 的记录被手工删掉、但表/列还在时也认得出来。"""
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        from app.rollback_migration_117 import rollback as _rollback_117
        assert _rollback_117(path, apply=True) == 0  # A06 的 117 在 116 之后，先回滚它
        before = _versions(path)
        for apply in (True, False):
            assert rollback(path, apply=apply) == 2
            err = capsys.readouterr().err
            assert "116_project_members_v2" in err and "rollback_migration_116" in err and "116 → 115" in err
        assert _versions(path) == before and _has_table(path) == 1
        assert not list(tmp_path.glob("app.db.before-rollback-115-*.db"))
        with sqlite3.connect(path) as connection:
            connection.execute("delete from schema_migrations where version like '116%'")
        assert rollback(path, apply=True) == 2
        assert "project_member_events" in capsys.readouterr().err
        assert _has_table(path) == 1 and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()


def test_main_exits_2_while_116_present(tmp_path, monkeypatch, capsys):
    """命令行入口（--yes --service-stopped，非交互）在 116 还在时退出码 2。"""
    from app import config
    from app.db import migrate
    from app.rollback_migration_115 import main

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        from app.rollback_migration_117 import rollback as _rollback_117
        assert _rollback_117(path, apply=True) == 0  # A06 的 117 在 116 之后，先回滚它
        assert main(["--yes", "--service-stopped"]) == 2
        assert "rollback_migration_116" in capsys.readouterr().err
        assert _has_table(path) == 1 and _recorded(path) == 1
    finally:
        config.get_settings.cache_clear()
