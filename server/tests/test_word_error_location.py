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
