"""A06：迁移 117 可单独回滚（导出版本 CSV、整库备份、当前已发布结构写回模板），回滚后结构与 116 状态一致，
旧程序（clean_template + importStructure）新建文档得到最新已发布的结构；重新迁移会再建回第 1 版。"""

import csv
import copy
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.rollback_migration_117 import TABLE, VERSION, rollback
from tests.test_document_objects_migration import _migrate_until
from tests.test_rollback_migration_116 import _schema

SRS = next((Path(__file__).resolve().parents[2] / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _recorded(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("select count(*) from schema_migrations where version = ?", (VERSION,)).fetchone()[0]


def test_rollback_117_writes_back_latest_published_and_restores_116_schema(tmp_path, monkeypatch, capsys):
    from app import config, db
    from app.db import connect, migrate
    from app.files import resolve_object, store_bytes
    from app.identity import create_user
    from app.template_import import clean_template

    reference = tmp_path / "ref"
    reference.mkdir()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{reference / 'app.db'}")
    config.get_settings.cache_clear()
    _migrate_until(reference, monkeypatch, 116)
    expected = _schema(reference / "app.db")

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        assert db.database_path() == path
        create_user(account="rb117-admin", name="回滚管理员", password="rollback-117-pw", roles=["system_admin"])
        from app.main import create_app
        with TestClient(create_app()) as client:
            token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
            login = client.post("/api/v1/auth/login", json={"account": "rb117-admin", "password": "rollback-117-pw"}, headers={"X-CSRF-Token": token})
            headers = {"X-CSRF-Token": login.json()["data"]["csrfToken"]}
            file = store_bytes(name="SRS.docx", media_type=DOCX, purpose="template", content=SRS.read_bytes())
            group = client.post("/api/v1/templates", json={"name": "回滚模板", "category": "438C"}, headers=headers).json()["data"]
            structure = client.post("/api/v1/template-documents/parse", json={"wordFileId": str(file.id)}, headers=headers).json()["data"]
            structure["reviewed"] = True
            td = client.post(f"/api/v1/templates/{group['id']}/documents",
                             json={"name": "需求", "wordFileId": str(file.id), "importStructure": structure}, headers=headers).json()["data"]
            base = client.get(f"/api/v1/template-documents/{td['id']}/draft").json()["data"]["base"]
            draft = copy.deepcopy(base["structure"])
            heading = [block for block in draft["blocks"] if block["kind"] == "heading" and not block["lock"]["text"]][3]
            heading["text"] += "（第二版）"
            guidance = next(block for block in draft["blocks"] if block["kind"] == "guidance" and block.get("path"))
            guidance["text"] += "（第二版指导）"
            assert client.put(f"/api/v1/template-documents/{td['id']}/draft", json={"structure": draft, "expectedDraftRevision": 0}, headers=headers).status_code == 200
            published = client.post(f"/api/v1/template-documents/{td['id']}/versions", json={"draftRevision": 1, "note": "第二版"}, headers=headers)
            assert published.status_code == 201, published.text
        from app.rollback_migration_118 import rollback as rollback_118
        assert rollback(path, apply=True) == 2   # A07：118 还在，先回滚 118
        assert rollback_118(path, apply=True) == 0
        assert _recorded(path) == 1
        assert rollback(path, apply=False) == 0
        assert rollback(path, apply=True) == 3   # 有第 2 版：默认拒绝
        assert _recorded(path) == 1
        assert rollback(path, apply=True, force=True) == 0
        assert _recorded(path) == 0
        assert _schema(path) == expected
        exported = list(tmp_path.glob(f"app.db.{TABLE}-117-*.csv"))
        assert len(exported) == 1
        with exported[0].open(encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        assert sorted(row["version"] for row in rows) == ["1", "2"]
        assert list(tmp_path.glob("app.db.before-rollback-117-*.db"))
        # 旧程序视角：importStructure + word_file_id 是第 2 版，clean_template 能用
        with sqlite3.connect(path) as connection:
            word_file_id, format_template = connection.execute("select word_file_id, format_template from template_documents where id = ?", (td["id"],)).fetchone()
            storage_key = connection.execute("select storage_key from files where id = ?", (word_file_id,)).fetchone()[0]
        legacy = json.loads(format_template)["importStructure"]
        assert word_file_id != str(file.id)
        assert any(block["text"] == heading["text"] for block in legacy["blocks"])
        assert any(block["text"] == guidance["text"] for block in legacy["blocks"])
        from docx import Document
        from io import BytesIO
        cleaned = clean_template(resolve_object(config.get_settings().file_data_dir, storage_key), legacy)
        assert any(paragraph.text == heading["text"] for paragraph in Document(BytesIO(cleaned)).paragraphs)
        assert rollback(path, apply=True) == 0
        assert "不需要回滚" in capsys.readouterr().out
        # 重新迁移：117 再建回来，第 1 版 = 写回的结构
        assert migrate() == ["117_template_versions.sql", "118_document_assignments.sql"]
        from app.migrate_template_versions import backfill
        report = backfill(backup=False)
        assert report["published"] == 1
        with connect() as connection:
            row = connection.execute("select version, source_file_id from template_document_versions where template_document_id = %s", (td["id"],)).fetchone()
            assert row["version"] == 1 and str(row["source_file_id"]) == word_file_id
    finally:
        config.get_settings.cache_clear()


def test_rollback_117_refuses_when_later_migration_recorded(tmp_path, monkeypatch, capsys):
    """23 M-2：schema_migrations 里有比 117 更新的版本时拒绝回滚（退出码 2），列出这些版本，库不变。"""
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
        assert not list(tmp_path.glob("app.db.before-rollback-117-*.db"))
    finally:
        config.get_settings.cache_clear()
