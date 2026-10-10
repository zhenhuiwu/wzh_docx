"""F2 / 用例 34：Word 写回失败返回 422 AI_FORMAT_INVALID 并带位置。"""

from io import BytesIO

from docx import Document

from tests.test_documents import create_document, document_context  # noqa: F401
from app.document_location import LocItem, compute_locations, location_text


def test_locations_count_direct_blocks_only():
    items = [LocItem("paragraph", "前言"), LocItem("heading", "3 需求", 1), LocItem("paragraph", "a"),
             LocItem("heading", "3.1 子节", 2), LocItem("paragraph", "b"), LocItem("paragraph", "表 3-1 质量等级"),
             LocItem("table", "x"), LocItem("heading", "3.2 另一节", 2), LocItem("list_item", "1"), LocItem("list_item", "2")]
    locations = compute_locations(items)
    assert locations[0] == {"headingPath": [], "ordinal": 1, "kind": "paragraph", "label": None}
    assert locations[4]["headingPath"] == ["3 需求", "3.1 子节"] and locations[4]["ordinal"] == 1
    assert locations[6]["label"] == "表 3-1 质量等级" and locations[6]["ordinal"] == 1
    assert locations[3] == {"headingPath": ["3 需求"], "ordinal": None, "kind": "heading", "label": None}
    assert locations[9]["headingPath"] == ["3 需求", "3.2 另一节"] and locations[9]["ordinal"] == 2
    assert location_text(locations[0]) == "文档开头（第一个标题之前） · 第 1 段"
    assert location_text(locations[6]) == "3 需求 › 3.1 子节 · 表 3-1 质量等级"


def test_multi_paragraph_cell_edit_reports_location(document_context):
    client, headers, project, _, _, _ = document_context
    word = Document()
    word.add_heading("封面", level=1)
    table = word.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "项目", "说明"
    table.cell(1, 0).text = "密级"
    table.cell(1, 1).paragraphs[0].text = "第一段"
    table.cell(1, 1).add_paragraph("第二段")
    buffer = BytesIO()
    word.save(buffer)
    from app.files import store_bytes
    stored = store_bytes(name="多段.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         purpose="attachment", project_id=project, content=buffer.getvalue())
    created = client.post(f"/api/v1/projects/{project}/documents",
                          json={"name": "多段单元格", "sourceType": "upload", "wordFileId": str(stored.id)}, headers=headers)
    assert created.status_code == 200, created.text
    document = created.json()["data"]
    markdown = document["markdownContent"].replace("第一段<br>第二段", "第一段<br>已修改")
    assert markdown != document["markdownContent"]
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": markdown}, headers=headers)
    assert saved.status_code == 422
    error = saved.json()["error"]
    assert error["code"] == "WORD_BLOCK_PROTECTED"  # 测试 L3
    assert error["details"]["reason"].startswith("表格第 2 行第 2 列包含多段文字")
    assert error["details"]["location"] == {"headingPath": ["封面"], "ordinal": 1, "kind": "table", "label": None}
    assert error["message"].startswith("封面 · 第 1 个表格：表格第 2 行第 2 列包含多段文字")


def test_refused_paragraph_delete_reports_deleted_text(document_context):
    """界面复验 N1：删除含书签的段落被拒时，details.deletedText 给出被删段落的原文字，界面据此插回原位置。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from app.files import store_bytes

    client, headers, project, _, _, _ = document_context
    word = Document()
    word.add_heading("1.1 标识", level=2)
    paragraph = word.add_paragraph()
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "1")
    start.set(qn("w:name"), "OLE_LINK1")
    paragraph._p.append(start)
    paragraph.add_run("文档标识号：TN/x")
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "1")
    paragraph._p.append(end)
    word.add_paragraph("其后一段。")
    buffer = BytesIO()
    word.save(buffer)
    stored = store_bytes(name="标识.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         purpose="attachment", project_id=project, content=buffer.getvalue())
    created = client.post(f"/api/v1/projects/{project}/documents",
                          json={"name": "标识号", "sourceType": "upload", "wordFileId": str(stored.id)}, headers=headers)
    assert created.status_code == 200, created.text
    document = created.json()["data"]
    markdown = document["markdownContent"].replace("文档标识号：TN/x\n\n", "")
    assert markdown != document["markdownContent"]
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": markdown}, headers=headers)
    assert saved.status_code == 422
    error = saved.json()["error"]
    assert error["code"] == "WORD_BLOCK_PROTECTED"
    assert error["details"]["deletedText"] == "文档标识号：TN/x"
    assert "书签" in error["details"]["reason"]


def test_refused_delete_next_to_edited_paragraph_reports_deleted_text(tmp_path):
    """界面复验 N1：相邻段已改过（与原稿相比）时再删受保护段，同样按“删除”拒绝并带原文字。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from app.docx_export import WordEditError, edit_original_docx
    from app.docx_parser import parse_docx

    word = Document()
    word.add_heading("1.1 标识", level=2)
    word.add_paragraph("本文档适用于系统。")
    paragraph = word.add_paragraph()
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "1")
    start.set(qn("w:name"), "OLE_LINK1")
    paragraph._p.append(start)
    paragraph.add_run("文档标识号：TN/x")
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "1")
    paragraph._p.append(end)
    source = tmp_path / "src.docx"
    word.save(source)
    markdown = parse_docx(str(source)).markdown
    edited = markdown.replace("本文档适用于系统。\n\n文档标识号：TN/x", "本文档适用于系统（已改）。")
    assert edited != markdown
    try:
        edit_original_docx(edited, source, tmp_path / "out.docx")
    except WordEditError as error:
        assert error.removed_text == "文档标识号：TN/x"
        assert "无法安全删除" in str(error)
    else:
        raise AssertionError("应当拒绝删除受保护段")
