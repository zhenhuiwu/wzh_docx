"""Generated heading envelopes are removable; document structure is not."""

import pytest

from app.document_rules import DraftFormatError, DraftHeadingError, generated_section_body, validate_draft


BODY = "保留这段正文。\n\n| 项目 | 说明 |\n|---|---|\n| 范围 | 不得丢弃 |"


@pytest.mark.parametrize("wrapper,heading", [
    ("## 3.2 软件能力需求", "3.2 软件能力需求"),
    ("# 软件能力需求 ###", "3.2 软件能力需求"),
    ("软件能力需求\n===", "3.2 软件能力需求"),
    ("3.2 软件能力需求\n---", "软件能力需求"),
    ("## 一、范围", "第一章 范围"),
    ("## （一） 范围", "一、范围"),
    ("## 范围", "第一章 范围"),
])
def test_only_equivalent_leading_heading_is_unwrapped(wrapper, heading):
    assert generated_section_body(wrapper + "\n\n" + BODY, heading=heading) == BODY
    assert validate_draft(generated_section_body(wrapper + "\n\n" + BODY, heading=heading), {}) == BODY


@pytest.mark.parametrize("wrapper,heading", [
    ("## 其他要求", "软件能力需求"),
    ("## 3.3 软件能力需求", "3.2 软件能力需求"),
    ("## 第二章 范围", "第一章 范围"),
    ("## 范围补充", "范围"),
    ("其他要求\n===", "范围"),
])
def test_different_heading_is_preserved_for_structural_correction(wrapper, heading):
    source = wrapper + "\n\n" + BODY
    assert generated_section_body(source, heading=heading) == source
    with pytest.raises(DraftHeadingError):
        validate_draft(source, {}, heading=heading)


@pytest.mark.parametrize("suffix", ["\n\n### 子标题\n\n不能丢弃。", "\n\n范围\n---\n\n不能丢弃。"])
def test_internal_headings_and_all_following_content_are_preserved(suffix):
    source = "## 范围\n\n" + BODY + suffix
    result = generated_section_body(source, heading="范围")
    assert result == BODY + suffix
    with pytest.raises(DraftHeadingError):
        validate_draft(result, {})


def test_only_one_heading_envelope_is_removed_and_plain_title_is_not_deleted():
    assert generated_section_body("范围\n\n正文", heading="范围") == "范围\n\n正文"
    with pytest.raises(DraftHeadingError):
        validate_draft("范围\n\n正文", {}, heading="范围")
    result = generated_section_body("## 范围\n\n## 范围\n\n正文", heading="范围")
    assert result == "## 范围\n\n正文"
    with pytest.raises(DraftHeadingError):
        validate_draft(result, {})


@pytest.mark.parametrize("wrapper", ["## 第一章 范围\n\n", "范围\n===\n\n", "```markdown\n## 范围\n\n"])
def test_streaming_wrapper_tokens_do_not_leak_partial_headings(wrapper):
    source = wrapper + "正文。"
    for end in range(1, len(wrapper) + 1):
        result = generated_section_body(source[:end], heading="第一章 范围", complete=False)
        assert result == ""
    assert generated_section_body(source, heading="第一章 范围", complete=False) == "正文。"


@pytest.mark.parametrize("source", [
    "<h2>范围</h2>\n正文", "## 范围\n\n<p>正文</p>",
    "```python\n## 范围\n正文\n```", "```markdown\n## 范围\n正文",
    "## 范围\n\n正文\n```\n内部代码\n```",
])
def test_heading_unwrap_cannot_bypass_html_or_code_validation(source):
    with pytest.raises(DraftFormatError):
        generated_section_body(source, heading="范围")


def test_prose_envelope_and_heading_can_both_be_unwrapped():
    assert generated_section_body("```markdown\n## 范围\n\n" + BODY + "\n```", heading="范围") == BODY
    assert generated_section_body(BODY, heading="范围") == BODY
    assert generated_section_body("## 范围", heading="范围") == ""
    with pytest.raises(DraftFormatError):
        validate_draft(generated_section_body("## 范围", heading="范围"), {})
