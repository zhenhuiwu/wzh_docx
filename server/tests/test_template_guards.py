"""审查 R-3：变异测试存活项补测。每条都对应一个“注入后应当失败”的缺陷：
- 116 回滚不检查 117；
- PUT 草稿事务内不复核草稿 id（23 S-9）；
- 发布不核对生成的 Word 文字（变异 M1）；
- 发布事务内不复核草稿 revision / 当前版本（变异 M2）；
- 改样式不清除段落直接格式（变异 M7）。
"""

from __future__ import annotations

import copy
import sqlite3
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn

from app import template_versions as tv
from app import template_word as tw
from app.db import connect
from app.template_import import parse_template
from tests.test_template_versions import SRS, _base, _edit_text, _template, _top, api  # noqa: F401  (api 是 fixture)


def _body(structure: dict) -> dict:
    return next(block for block in _top(structure["blocks"]) if block["kind"] == "body" and not block["lock"]["text"])


def _put(api, td_id, structure, revision, styles=None, **extra):
    body = {"structure": structure, "expectedDraftRevision": revision, **extra}
    if styles is not None:
        body["format"] = {"styles": styles}
    return api.send("PUT", f"/template-documents/{td_id}/draft", body)


def _versions(td_id) -> int:
    with connect() as connection:
        return connection.execute("SELECT count(*) FROM template_document_versions WHERE template_document_id=%s AND status<>'draft'",
                                  (td_id,)).fetchone()[0]


def test_rollback_116_refuses_while_117_present(tmp_path, monkeypatch, capsys):
    from app import config
    from app.db import migrate
    from app.rollback_migration_116 import VERSION, rollback

    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path / "files"))
    config.get_settings.cache_clear()
    try:
        migrate()
        assert rollback(path, apply=True) == 2
        assert "117" in capsys.readouterr().err
        with sqlite3.connect(path) as connection:
            versions = {row[0] for row in connection.execute("select version from schema_migrations")}
        assert {VERSION, "117_template_versions"} <= versions
        assert not list(tmp_path.glob("app.db.before-rollback-116-*.db"))
    finally:
        config.get_settings.cache_clear()


def test_put_draft_rechecks_draft_id_inside_transaction(api, monkeypatch):
    """S-9：校验期间草稿被换成另一份同 revision 的草稿（ABA），保存必须 409，且不改写新草稿。"""
    td, _ = _template(api)
    td_id = td["id"]
    base = _base(api, td_id)
    body = _body(base["structure"])
    assert _put(api, td_id, _edit_text(base["structure"], body["id"], "（一）"), 0).status_code == 200
    draft = _base(api, td_id)
    real = tv.tm.validate_draft
    replaced = uuid4()

    def swap(*args, **kwargs):
        result = real(*args, **kwargs)
        with connect() as connection:
            connection.execute("UPDATE template_document_versions SET id=%s WHERE template_document_id=%s AND status='draft'", (replaced, td_id))
            connection.commit()
        return result

    monkeypatch.setattr(tv.tm, "validate_draft", swap)
    response = _put(api, td_id, _edit_text(draft["structure"], body["id"], "（二）"), draft["draftRevision"])
    assert response.status_code == 409, response.text
    with connect() as connection:
        row = connection.execute("SELECT revision FROM template_document_versions WHERE id=%s", (replaced,)).fetchone()
    assert row["revision"] == draft["draftRevision"]


def test_publish_verifies_generated_word_text(api, monkeypatch):
    """M1：生成的 Word 里没有草稿的文字修改时，发布必须拒绝（422），不产生新版本。"""
    td, _ = _template(api)
    td_id = td["id"]
    base = _base(api, td_id)
    assert _put(api, td_id, _edit_text(base["structure"], _body(base["structure"])["id"], "（应写进 Word）"), 0).status_code == 200
    before = _versions(td_id)

    def lazy(base_path, draft, base_styles, draft_styles, out_path):
        Path(out_path).write_bytes(Path(base_path).read_bytes())  # 注入：什么都没写进 Word
        return 1

    monkeypatch.setattr(tv.tw, "apply_draft", lazy)
    response = api.send("POST", f"/template-documents/{td_id}/versions", {"draftRevision": 1, "note": "不该成功"})
    assert response.status_code == 422, response.text
    assert _versions(td_id) == before


def _publish_with_hook(api, td_id, revision, hook, monkeypatch):
    real = tv.tw.clean_version

    def wrapped(*args, **kwargs):
        result = real(*args, **kwargs)
        hook()
        return result

    monkeypatch.setattr(tv.tw, "clean_version", wrapped)
    return api.send("POST", f"/template-documents/{td_id}/versions", {"draftRevision": revision, "note": "事务内复核"})


def test_publish_rechecks_draft_revision_inside_transaction(api, monkeypatch):
    """M2：生成 Word 期间草稿被改（revision 变了），发布必须 409，已发布版本不变。"""
    td, _ = _template(api)
    td_id = td["id"]
    base = _base(api, td_id)
    assert _put(api, td_id, _edit_text(base["structure"], _body(base["structure"])["id"], "（改）"), 0).status_code == 200
    before = _versions(td_id)

    def bump():
        with connect() as connection:
            connection.execute("UPDATE template_document_versions SET revision=revision+1 WHERE template_document_id=%s AND status='draft'", (td_id,))
            connection.commit()

    response = _publish_with_hook(api, td_id, 1, bump, monkeypatch)
    assert response.status_code == 409 and response.json()["error"]["code"] == "REVISION_CONFLICT", response.text
    assert _versions(td_id) == before


def test_publish_rechecks_current_version_inside_transaction(api, monkeypatch):
    """M2：生成 Word 期间当前版本变了，发布必须 409 TEMPLATE_DRAFT_STALE，不会出现两个 published。"""
    td, _ = _template(api)
    td_id = td["id"]
    base = _base(api, td_id)
    body_id = _body(base["structure"])["id"]
    assert _put(api, td_id, _edit_text(base["structure"], body_id, "（第 2 版）"), 0).status_code == 200
    assert api.send("POST", f"/template-documents/{td_id}/versions", {"draftRevision": 1, "note": "第 2 版"}).status_code == 201
    with connect() as connection:
        v1 = connection.execute("SELECT id FROM template_document_versions WHERE template_document_id=%s AND version=1", (td_id,)).fetchone()["id"]
    draft = _base(api, td_id)
    assert _put(api, td_id, _edit_text(draft["structure"], body_id, "（第 3 版）"), 0).status_code == 200

    def move_current():
        with connect() as connection:
            connection.execute("UPDATE template_documents SET current_version_id=%s WHERE id=%s", (v1, td_id))
            connection.commit()

    response = _publish_with_hook(api, td_id, 1, move_current, monkeypatch)
    assert response.status_code == 409 and response.json()["error"]["code"] == "TEMPLATE_DRAFT_STALE", response.text
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM template_document_versions WHERE template_document_id=%s AND status='published'",
                                  (td_id,)).fetchone()[0] == 1


def test_style_change_clears_direct_formatting(tmp_path):
    """M7（U-15）：改“正文”字号后，使用正文样式且自带字号的段落不再有直接字号；没改的属性（字体）原样保留。"""
    content = SRS.read_bytes()
    structure = parse_template(content)
    analysis = tw._analysis(content)
    doc = Document(BytesIO(content))
    impact = tw.impact_on(doc, [{"style": "正文", "prop": "size", "value": 15}], {})
    assert impact[0]["directFormatParagraphs"] > 0
    fonts_before = sum(1 for _ in doc.element.body.iter(qn("w:rFonts")))
    styles = copy.deepcopy(analysis["styles"])
    styles["正文"]["size"] = 15
    out = tmp_path / "out.docx"
    tw.apply_draft(SRS, structure, analysis["styles"], styles, out)
    after = Document(out)
    assert tw.impact_on(after, [{"style": "正文", "prop": "size", "value": 16}], {})[0]["directFormatParagraphs"] == 0
    assert sum(1 for _ in after.element.body.iter(qn("w:rFonts"))) == fonts_before
    assert tw.read_styles(after)["正文"]["size"] == 15
