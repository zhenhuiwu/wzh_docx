"""The skill's chapter and table rules are enforced outside the model prompt."""

import pytest

from app.document_rules import DraftFormatError, section_rules, validate_draft


def test_skill_rules_guard_word_body_and_tables():
    srs = section_rules(heading="3.2.1 自定义功能", document_name="软件需求规格说明")
    assert srs["skill"] == "word-fillter-438c-srs" and srs["chapter"]["id"] == "3.2"
    assert "content" not in srs["chapter"] and "generation_prompt" not in srs["chapter"]
    sdd = section_rules(heading="4.3.2 外部接口", template_name="软件设计说明", document_name="SRS")
    assert sdd["skill"] == "word-fillter-438c-sdd" and sdd["chapter"]["id"] == "4.3"
    assert not section_rules(heading="范围", document_name="通用说明")["skill"]
    refs = section_rules(heading="2 引用文档", document_name="软件需求规格说明")
    valid = "| 序号 | 名称 | 版本 | 发布日期 | 来源 |\n|---|---|---|---|---|\n|1|待补充|待补充|待补充|待补充|"
    assert validate_draft(valid, refs) == valid
    assert validate_draft("**要点**\n\n1. 已知内容\n2. 待补充", srs)
    invalid = [
        "## 新标题\n\n正文", "新标题\n====\n正文", "```xml\n正文\n```",
        '<p style="font-size:90px">正文</p>', "![图片](a.png)", "[链接](https://example.com)",
        "`代码`", "> 引用", '{"content":"正文"}', "- 第一项\n    - 嵌套项",
        valid.replace("| 来源 |", "| 新列 |"), valid + "\n| 2 | 待补充 |",
        valid.replace("|1|待补充|待补充|待补充|待补充|", "|1|待补充|待补充|待补充|待补充|多余字段|"),
    ]
    for content in invalid:
        with pytest.raises(DraftFormatError):
            validate_draft(content, refs)
    with pytest.raises(DraftFormatError):
        validate_draft("**加粗**", {}, selected_only=True)
    with pytest.raises(DraftFormatError):
        validate_draft("软件能力需求\n\n正文", srs, heading="3.2 软件能力需求")
    assert validate_draft("未完成的表格\n\n| 序号 |", refs, complete=False)
    assert validate_draft("普通改写文本。", {}, selected_only=True)
    # A type chosen in the request must remain enforced when the draft is adopted.
    from app.documents import DocumentEdit, _validate_edits
    row = [None] * 16
    row[2], row[7] = "通用说明", "# 2 引用文档\n\n正文"
    with pytest.raises(DraftFormatError):
        _validate_edits(row, [DocumentEdit(headingIndex=0, content="正文")], "按照软件需求规格说明编写")
    assert _validate_edits(row, [DocumentEdit(headingIndex=0, content=valid)], "按照软件需求规格说明编写")


def test_chapter_edit_keeps_original_and_visio_images_in_order():
    existing = "原文\n\n![原图](image_0.png)\n\n![流程图](docx-image:diagram-example)"
    edited = existing.replace("原文", "修改后的说明")
    assert validate_draft(edited, {}, preserve_images=True, existing=existing) == edited
    assert validate_draft("修改后的说明\n\n![原图](image_0.png)", {}, complete=False,
                          preserve_images=True, existing=existing)
    for invalid in ("只有正文", edited.replace("image_0.png", "https://example.com/image.png"),
                    edited.replace("![原图](image_0.png)\n\n", ""),
                    "![流程图](docx-image:diagram-example)\n\n![原图](image_0.png)"):
        with pytest.raises(DraftFormatError):
            validate_draft(invalid, {}, preserve_images=True, existing=existing)
    with pytest.raises(DraftFormatError):
        validate_draft("![流程图](docx-image:diagram-example)", {}, complete=False,
                       preserve_images=True, existing=existing)
