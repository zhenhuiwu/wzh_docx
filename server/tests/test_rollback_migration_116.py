"""A05：迁移 116 可单独回滚（先导出审计 CSV、整库备份），回滚后的结构与 115 状态一致，重新迁移能再建回来。"""

import csv
import sqlite3
from uuid import uuid4

from app.rollback_migration_116 import MEMBER_INDEX, TABLE, VERSION, rollback
from tests.test_document_objects_migration import _migrate_until


def _schema(path):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        indexes = sorted(row[0] for row in connection.execute(
            "select name from sqlite_master where type='index' and name not like 'sqlite_%'"))
        return {name: [tuple(row[1:]) for row in connection.execute(f"pragma table_info({name})")] for name in tables}, indexes


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_116_restores_the_115_schema_and_keeps_members(tmp_path, monkeypatch, capsys):
    from app import config, db
    from app.db import connect, migrate
    from app.identity import create_user

    reference = tmp_path / "ref"
    reference.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{reference / 'app.db'}")
    config.get_settings.cache_clear()
    _migrate_until(reference, monkeypatch, 115)
    expected = _schema(reference / "app.db")

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        assert db.database_path() == path
        owner = create_user(account="rb116-owner", name="回滚负责人", password="rollback-116-pw", roles=["project_creator"])
        project = uuid4()
        with connect() as connection:
            connection.execute("insert into projects(id, name, owner_id) values (%s, %s, %s)", (project, "回滚项目", owner))
            connection.execute("""insert into project_members(project_id, user_id, role, created_at, updated_at, updated_by)
                                  values (%s, %s, 'owner', now(), now(), %s)""", (project, owner, owner))
            connection.execute("""insert into project_member_events(id, project_id, actor_id, target_user_id, action, new_role, members_revision)
                                  values (%s, %s, %s, %s, 'add', 'owner', 1)""", (uuid4(), project, owner, owner))
            connection.commit()
        assert _recorded(path) == 1
        # 回滚顺序 117 → 116（A06 的 117 在 116 之后）
        from app.rollback_migration_117 import rollback as rollback_117
        assert rollback_117(path, apply=True) == 0
        assert rollback(path, apply=False) == 0  # 只检查
        assert _recorded(path) == 1 and _schema(path) != expected
        assert rollback(path, apply=True) == 0
        assert _recorded(path) == 0
        assert _schema(path) == expected
        assert MEMBER_INDEX not in _schema(path)[1]
        exported = list(tmp_path.glob(f"app.db.{TABLE}-116-*.csv"))
        assert len(exported) == 1
        with exported[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        assert [(row["action"], row["new_role"]) for row in rows] == [("add", "owner")]
        assert list(tmp_path.glob("app.db.before-rollback-116-*.db"))
        with sqlite3.connect(path) as connection:  # 成员关系本身保留
            assert connection.execute("select role from project_members where project_id = ?", (str(project),)).fetchone()[0] == "owner"
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        assert migrate() == ["116_project_members_v2.sql", "117_template_versions.sql"]
        assert _recorded(path) == 1
        with sqlite3.connect(path) as connection:
            assert connection.execute("select members_revision from projects where id = ?", (str(project),)).fetchone()[0] == 1
            assert connection.execute(f"select count(*) from {TABLE}").fetchone()[0] == 0
    finally:
        config.get_settings.cache_clear()


def test_migration_116_on_a_115_database_keeps_old_rows(tmp_path, monkeypatch):
    """17 §2.7：在 115 的库上执行 116：列存在，旧行 created_at 为 NULL，members_revision=1；重复 migrate 不报错；旧成员可读。"""
    from app import config
    from app.db import connect, migrate
    from app.identity import create_user

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    config.get_settings.cache_clear()
    try:
        _migrate_until(tmp_path, monkeypatch, 115)
        owner = create_user(account="m116-owner", name="旧负责人", password="migration-116-pw", roles=["project_creator"])
        project = uuid4()
        with connect() as connection:
            connection.execute("insert into projects(id, name, owner_id) values (%s, %s, %s)", (project, "旧项目", owner))
            connection.execute("insert into project_members(project_id, user_id, role) values (%s, %s, 'owner')", (project, owner))
            connection.commit()
        assert migrate() == ["116_project_members_v2.sql", "117_template_versions.sql"]
        assert migrate() == []
        with sqlite3.connect(tmp_path / "app.db") as connection:
            assert connection.execute("select members_revision from projects where id = ?", (str(project),)).fetchone()[0] == 1
            assert connection.execute("select created_at, updated_at, updated_by from project_members where project_id = ?",
                                      (str(project),)).fetchone() == (None, None, None)
            assert connection.execute(f"select count(*) from {TABLE}").fetchone()[0] == 0
    finally:
        config.get_settings.cache_clear()


def test_rollback_116_refuses_while_a_newer_migration_is_recorded(tmp_path, monkeypatch, capsys):
    """顺序回滚同一套路：schema_migrations 里有更新的迁移（如 A06 的 117）时拒绝回滚 116，库不变。"""
    from app import config
    from app.db import migrate

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        from app.rollback_migration_117 import rollback as _rollback_117
        assert _rollback_117(path, apply=True) == 0  # A06 的 117 在 116 之后，先回滚它
        with sqlite3.connect(path) as connection:
            connection.execute("insert into schema_migrations(version) values ('117_future_probe')")
        assert rollback(path, apply=True) == 2
        err = capsys.readouterr().err
        assert "117_future_probe" in err and "rollback_migration_117" in err and "117 → 116" in err
        assert _recorded(path) == 1
        assert not list(tmp_path.glob("app.db.before-rollback-116-*.db"))
    finally:
        config.get_settings.cache_clear()
