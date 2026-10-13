"""S4：迁移 111–113、惰性回填、重新对齐、down 脚本、结构守卫（01 §8.2 用例 5–11）。"""
from tests.migration_chain import files_from, rollback_newer_than

import shutil
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import app.db as db
from app.db import connect, migrate
from app.document_objects import check_invariants, ensure_document_objects, live_objects
from app.identity import create_user
from tests.test_documents import create_document, document_context  # noqa: F401

MIGRATIONS = Path(db.__file__).resolve().parent.parent / "migrations"
DOWN = Path(db.__file__).resolve().parent.parent / "migrations_down" / "113_111_down.sql"
NEW_TABLES = ("document_objects", "document_changes", "document_object_changes", "document_conflicts", "document_conflict_items")


def _migrate_until(tmp_path, monkeypatch, last: int) -> list[str]:
    partial = tmp_path / f"migrations_{last}"
    partial.mkdir()
    for path in MIGRATIONS.glob("*.sql"):
        if int(path.stem.split("_", 1)[0]) <= last:
            shutil.copy(path, partial / path.name)
    monkeypatch.setattr(db, "migrations_dir", lambda: partial)
    try:
        return migrate()
    finally:
        monkeypatch.setattr(db, "migrations_dir", lambda: MIGRATIONS)


def _schema(connection) -> dict:
    tables = [row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
    return {name: [(row[1], row[2], row[3], row[5]) for row in connection._conn.execute(f"PRAGMA table_info({name})")]
            for name in tables}


def test_legacy_109_database_upgrades_and_backfills_lazily(tmp_path, monkeypatch):
    # 用例 5：109 状态旧库 → migrate → 110–113；旧文档可读；owner 成员补齐
    _migrate_until(tmp_path, monkeypatch, 109)
    owner = create_user(account="m111-owner", name="旧负责人", password="migration-111-pw", roles=["project_creator"])
    project, document = uuid4(), uuid4()
    text = "# 范围\n\n旧正文。\n\n- 列表一\n- 列表二\n"
    with connect() as connection:
        connection.execute("INSERT INTO projects(id,name,owner_id) VALUES(%s,'旧项目',%s)", (project, owner))
        connection.execute("INSERT INTO documents (id, project_id, name, owner_id, markdown_content, revision) "
                           "VALUES (%s,%s,'旧文档',%s,%s,3)", (document, project, owner, text))
        connection.commit()
    assert migrate() == ["110_project_member_owner_backfill.sql", "111_document_objects.sql",
                         "112_document_changes.sql", "113_document_conflicts.sql", *files_from(114)]
    with connect() as connection:
        assert connection.execute("SELECT role FROM project_members WHERE project_id=%s", (project,)).fetchone()["role"] == "owner"
        row = connection.execute("SELECT objects_revision, updated_by, revision FROM documents WHERE id=%s", (document,)).fetchone()
        assert row["objects_revision"] is None and row["updated_by"] is None and row["revision"] == 3
        versions_before = connection.execute("SELECT COUNT(*) AS n FROM document_versions").fetchone()["n"]
        # 用例 6：回填后 I1、I2 成立，revision 不变，不新增版本快照
        assert ensure_document_objects(connection, document)
        connection.commit()
        assert check_invariants(connection, document)
        assert connection.execute("SELECT revision FROM documents WHERE id=%s", (document,)).fetchone()["revision"] == 3
        assert connection.execute("SELECT COUNT(*) AS n FROM document_versions").fetchone()["n"] == versions_before
        assert [row["kind"] for row in live_objects(connection, document)] == ["heading", "paragraph", "list_item", "list_item"]
        kinds = [row["kind"] for row in connection.execute(
            "SELECT kind FROM document_changes WHERE document_id=%s", (document,)).fetchall()]
        assert kinds == ["backfill"]
        # 第二次 ensure 不重复
        assert ensure_document_objects(connection, document)
        assert len(live_objects(connection, document)) == 4


def test_lazy_backfill_through_api_and_round_trip_failure(document_context, monkeypatch):  # noqa: F811
    client, headers, *_ = document_context
    document = create_document(document_context)
    did = UUID(document["id"])
    response = client.get(f"/api/v1/documents/{did}/objects")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["objectModel"] is True and data["documentRevision"] == 0
    assert [item["kind"] for item in data["objects"]][:2] == ["heading", "paragraph"]
    with connect() as connection:
        assert check_invariants(connection, did)
    # 用例 7：往返失败（构造）→ objects_revision 保持 NULL，保存走严格路径且成功
    other = create_document(document_context, name="不可往返")
    import app.document_objects as objects
    monkeypatch.setattr(objects, "assemble", lambda prefix, blocks: "\x00不一致")
    assert client.get(f"/api/v1/documents/{other['id']}/objects").json()["data"]["objectModel"] is False
    content = other["markdownContent"].replace("原始正文。", "严格路径保存。")
    saved = client.patch(f"/api/v1/documents/{other['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": content}, headers=headers)
    assert saved.status_code == 200, saved.text
    with connect() as connection:
        row = connection.execute("SELECT objects_revision, revision, updated_by FROM documents WHERE id=%s", (other["id"],)).fetchone()
        assert row["objects_revision"] is None and row["revision"] == 1 and row["updated_by"] is not None


def test_second_migrate_is_noop_and_wal():
    # 用例 8
    migrate()
    assert migrate() == []
    assert db.journal_mode() == "wal"


def test_realign_after_legacy_write_keeps_ids(document_context, monkeypatch):  # noqa: F811
    # 用例 9：模拟旧代码直接改 markdown 并 revision+1 → 下次写入时未改块 ID 保留、改块 revision+1
    client, headers, *_ = document_context
    monkeypatch.setattr("app.documents._check_word", lambda *args, **kwargs: None)
    document = create_document(document_context)
    did = UUID(document["id"])
    text = "# 范围\n\n第一段。\n\n第二段。\n\n第三段。\n"
    assert client.patch(f"/api/v1/documents/{did}/content", json={"expectedRevision": 0, "markdownContent": text},
                        headers=headers).status_code == 200
    with connect() as connection:
        before = {row["body"]: (row["id"], row["revision"]) for row in live_objects(connection, did)}
        connection.execute("UPDATE documents SET markdown_content=%s, revision=revision+1 WHERE id=%s",
                           (text.replace("第二段。", "旧代码改过的第二段。"), did))
        connection.commit()
    saved = client.patch(f"/api/v1/documents/{did}/content",
                         json={"expectedRevision": 2, "markdownContent": text.replace("第二段。", "旧代码改过的第二段。")
                               .replace("第三段。", "新代码改第三段。")}, headers=headers)
    assert saved.status_code == 200, saved.text
    with connect() as connection:
        after = live_objects(connection, did)
        assert check_invariants(connection, did)
        by_id = {row["id"]: row for row in after}
        assert by_id[before["第一段。"][0]]["revision"] == before["第一段。"][1]
        assert by_id[before["第二段。"][0]]["body"] == "旧代码改过的第二段。"
        assert by_id[before["第二段。"][0]]["revision"] == before["第二段。"][1] + 1
        assert by_id[before["第三段。"][0]]["body"] == "新代码改第三段。"
        kinds = [row["kind"] for row in connection.execute(
            "SELECT kind FROM document_changes WHERE document_id=%s ORDER BY doc_revision, created_at", (did,)).fetchall()]
        assert kinds == ["backfill", "edit", "realign", "edit"]


def test_down_script_restores_110_schema(tmp_path, monkeypatch):
    # 用例 10：down 后结构与 110 状态一致；之后可再次升级
    _migrate_until(tmp_path, monkeypatch, 110)
    with connect() as connection:
        expected = _schema(connection)
    assert migrate() == ["111_document_objects.sql", "112_document_changes.sql", "113_document_conflicts.sql",
                         *files_from(114)]
    # 116/117 与 111–115 无关，按回滚顺序先单独回滚 117、116，再执行 113→111 的 down 脚本
    from app.rollback_migration_117 import rollback as rollback_117
    assert rollback_newer_than(db.database_path(), 117) == 0 and rollback_117(db.database_path(), apply=True) == 0
    from app.rollback_migration_116 import rollback as rollback_116
    assert rollback_116(db.database_path(), apply=True) == 0
    with connect() as connection:
        connection._conn.executescript(DOWN.read_text(encoding="utf-8"))
        assert _schema(connection) == expected
    assert len(migrate()) == 3 + len(files_from(114))  # 111–113 + 回滚链


def test_down_script_keeps_legacy_document_tests_green(document_context):  # noqa: F811
    # 用例 10 后半：down 后旧代码路径（只读写 documents）仍工作——用 SQL 直接模拟旧版保存
    document = create_document(document_context)
    with connect() as connection:
        connection._conn.executescript(DOWN.read_text(encoding="utf-8"))
        connection.execute("UPDATE documents SET markdown_content=markdown_content || '\n\n补充。', revision=revision+1 "
                           "WHERE id=%s", (document["id"],))
        connection.commit()
        assert "objects_revision" not in [row[1] for row in connection._conn.execute("PRAGMA table_info(documents)")]


def test_new_tables_respect_column_naming_guards():
    # 用例 11：新表无 content/blocks/outline/format_template 列；*_id 列只存 UUID
    migrate()
    with connect() as connection:
        for table in NEW_TABLES:
            columns = [row[1] for row in connection._conn.execute(f"PRAGMA table_info({table})")]
            assert not {"content", "blocks", "outline", "format_template"} & set(columns), table
            assert all(not name.endswith("_id") or name in {"document_id", "actor_id", "change_id", "object_id",
                                                            "conflict_id", "origin_object_id"} for name in columns), table
        for name in ("objects_revision", "objects_prefix", "updated_by"):
            assert name in [row[1] for row in connection._conn.execute("PRAGMA table_info(documents)")]


def test_uuid_columns_hold_uuids(document_context):  # noqa: F811
    client, headers, *_ = document_context
    document = create_document(document_context)
    content = document["markdownContent"].replace("原始正文。", "改一处。")
    assert client.patch(f"/api/v1/documents/{document['id']}/content",
                        json={"expectedRevision": 0, "markdownContent": content}, headers=headers).status_code == 200
    with connect() as connection:
        for table, columns in {"document_objects": ("id", "document_id"), "document_changes": ("id", "document_id", "actor_id"),
                               "document_object_changes": ("change_id", "object_id")}.items():
            for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall():
                for column in columns:
                    assert row[column] is None or isinstance(row[column], UUID), (table, column, row[column])


def test_raw_sqlite_has_no_foreign_key_violations(document_context):  # noqa: F811
    client, headers, *_ = document_context
    create_document(document_context)
    with connect() as connection:
        assert list(connection._conn.execute("PRAGMA foreign_key_check")) == []
    assert sqlite3.sqlite_version_info >= (3, 35)


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
