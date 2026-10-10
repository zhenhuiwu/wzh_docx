"""工作台重塑轮：上轮发现的两个保存/预览缺陷。"""

import pytest

from app.docx_export import _html_blocks
from tests.test_documents import create_document, document_context  # noqa: F401


def test_html_comments_are_dropped_and_empty_headings_ignored():
    blocks = _html_blocks("# 范围\n\n<!-- 注释 -->\n\n正文<!-- 行内注释 -->继续。\n\n#\n\n## \n\n### 有字的标题")
    assert [block.tag for block in blocks] == ["h1", "p", "h3"]
    assert "".join(blocks[1].itertext()) == "正文继续。"
    assert all("注释" not in "".join(block.itertext()) for block in blocks)


@pytest.mark.parametrize("addition", ["<!-- 注释 -->", "正文<!-- 注释 -->继续。", "#", "## "])
def test_comment_and_empty_heading_save_and_preview(document_context, addition):  # noqa: F811
    client, headers, *_ = document_context
    document = create_document(document_context, name=f"缺陷-{addition}")
    content = document["markdownContent"].replace("原始正文。", f"原始正文。\n\n{addition}\n\n")
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": content}, headers=headers)
    # 以前：注释保存返回英文 lxml 报错（422）；只写“#”时保存成功但预览 422
    assert saved.status_code == 200, saved.text
    preview = client.get(f"/api/v1/documents/{document['id']}/preview")
    assert preview.status_code == 200, preview.text
    assert "注释" not in preview.json()["data"]["html"]
