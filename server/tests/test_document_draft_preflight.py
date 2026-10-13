"""Finished chapter drafts must offer only adoption modes the native Word can keep."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from docx import Document
from docx.oxml import OxmlElement

import app.documents as module
from app.docx_export import edit_original_docx
from app.docx_parser import parse_docx
from app.errors import AppError


@pytest.fixture
def route_context(tmp_path, monkeypatch):
    source = tmp_path / "original.docx"
    word = Document()
    word.add_heading("目标", level=1)
    paragraph = word.add_paragraph("旧正文，日期域必须保留。")
    for tag, value in (("w:fldChar", "begin"), ("w:instrText", " DATE "), ("w:fldChar", "end")):
        run, node = OxmlElement("w:r"), OxmlElement(tag)
        if tag == "w:fldChar":
            node.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fldCharType", value)
        else:
            node.text = value
        run.append(node)
        paragraph._p.append(run)
    word.add_heading("其他章节", level=1)
    word.add_paragraph("其他章节保持不变。")
    word.save(source)
    row = [None] * 16
    row[0], row[1], row[2], row[7], row[12], row[15] = uuid4(), uuid4(), "中性文档", parse_docx(str(source)).markdown, 7, ""
    settings = SimpleNamespace(ai_base_url="http://localhost/v1", ai_model="offline-test", ai_api_key="", ai_context_tokens=32000)
    request = SimpleNamespace(state=SimpleNamespace(request_id="preflight-test"))
    monkeypatch.setattr(module, "connect", lambda: MagicMock())
    monkeypatch.setattr(module, "section_memory_targets", lambda *args: {})
    monkeypatch.setattr(module, "document_principal", lambda *args: SimpleNamespace(roles=("system_admin",), user_id=uuid4()))
    monkeypatch.setattr(module, "_document_row", lambda *args: row)
    # 本组只测真实 Word 预检；对象/分工持久化由 object_guidance / assignments 的 API 测试覆盖。
    def spans(_connection, current):
        return [{"id": str(uuid4()), "kind": "paragraph", "body": text, "label": text,
                 "headingIndex": index, "start": current[7].index(text), "end": current[7].index(text) + len(text)}
                for index, text in enumerate(("旧正文，日期域必须保留。", "其他章节保持不变。"))]
    monkeypatch.setattr("app.document_guidance.object_spans", spans)
    monkeypatch.setattr("app.document_assignments.require_scope_write", lambda *args: None)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    def operations(kwargs):
        # Spec 30 §4: a section with body returns block operations; add after the protected paragraph.
        scope = kwargs["block_scope"]
        return json.dumps({"operations": [{"op": "insertAfter", "blockId": scope["blocks"][-1]["id"], "content": "清晰的新正文。"}]}, ensure_ascii=False)

    monkeypatch.setattr(module, "draft_section_text", lambda **kwargs: operations(kwargs))

    async def generate(**kwargs):
        yield {"type": "progress", "stage": "writing", "message": "正在生成正文"}
        yield operations(kwargs)

    monkeypatch.setattr(module, "stream_section_text", generate)
    checked = []

    def check_word(connection, current, content):
        checked.append(content)
        try:
            edit_original_docx(content, source, tmp_path / "checked.docx")
        except ValueError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None

    monkeypatch.setattr(module, "_check_word", check_word)
    return request, row, source, tmp_path, checked


def stream_events(response):
    async def collect():
        return [json.loads(chunk) async for chunk in response.body_iterator]

    return asyncio.run(collect())


@pytest.mark.parametrize("streaming", [False, True])
def test_protected_word_chapter_keeps_field_when_ai_inserts_in_place(route_context, streaming):
    request, row, source, tmp_path, checked = route_context
    original_bytes = source.read_bytes()
    response = module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0), stream=streaming)
    if streaming:
        events = stream_events(response)
        assert events[-1]["type"] == "done", events
        result = events[-1]
    else:
        result = response["data"]
    assert result["operations"][0]["op"] == "insertAfter" and result["operations"][0]["content"] == "清晰的新正文。"
    assert result["allowedModes"] == ["replace"]
    assert result["content"].startswith("旧正文，日期域必须保留。") and result["content"].endswith("清晰的新正文。")
    assert len(checked) == 1 and row[12] == 7 and source.read_bytes() == original_bytes
    adopted = module._section_update(row, 0, result["content"], "replace")
    edit_original_docx(adopted, source, tmp_path / "adopted.docx")
    final_word = Document(tmp_path / "adopted.docx")
    assert final_word.element.body.xpath(".//w:fldChar")
    assert "旧正文，日期域必须保留。" in adopted and "其他章节保持不变。" in adopted


@pytest.mark.parametrize("streaming", [False, True])
def test_draft_has_no_success_event_when_neither_word_adoption_mode_is_available(route_context, monkeypatch, streaming):
    request, row, source, _, _ = route_context
    before = source.read_bytes()

    def blocked(*args):
        raise AppError(422, "AI_FORMAT_INVALID", "原稿无法安全修改。")

    monkeypatch.setattr(module, "_check_word", blocked)
    body = module.SectionDraftBody(expectedRevision=7, headingIndex=0)
    if streaming:
        events = stream_events(module.draft_section(request, str(row[0]), body, stream=True))
        assert events[-1]["type"] == "error" and events[-1]["message"] == "原稿无法安全修改。"
        assert not any(event["type"] == "done" for event in events)
    else:
        with pytest.raises(AppError) as failure:
            module.draft_section(request, str(row[0]), body)
        assert failure.value.code == "AI_FORMAT_INVALID"
    assert source.read_bytes() == before and row[12] == 7


def test_plain_word_draft_can_offer_both_adoption_modes(route_context):
    _, row, source, _, _ = route_context
    word = Document()
    word.add_heading("目标", level=1)
    word.add_paragraph("旧正文。")
    word.save(source)
    row[7] = parse_docx(str(source)).markdown
    assert module._section_adoption_modes(None, row, 0, "新正文。") == ["replace", "append"]


def test_selection_draft_does_not_claim_preflight_without_a_real_selection_range(route_context):
    request, row, _, _, checked = route_context
    with pytest.raises(AppError) as rejected:
        module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0,
                             selectedText="旧正文", scope="selection"))
    assert rejected.value.code == "SELECTION_REQUIRED" and checked == []


def test_stream_progress_keeps_draft_and_checks_word_before_done(route_context, monkeypatch):
    request, row, _, _, checked = route_context

    async def generate(**kwargs):
        assert kwargs["include_progress"] is True
        yield {"type": "progress", "stage": "planning", "message": "正在确认本次修改范围"}
        yield json.dumps({"operations": [{"op": "insertAfter", "blockId": kwargs["block_scope"]["blocks"][0]["id"],
                                          "content": "清晰的新正文。"}]}, ensure_ascii=False)

    monkeypatch.setattr(module, "stream_section_text", generate)
    events = stream_events(module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0), stream=True))
    # Block operations are only shown once complete (spec 30 §4): no partial draft events.
    assert [event["type"] for event in events] == ["start", "progress", "progress", "done"]
    assert [(event["stage"], event["message"]) for event in events if event["type"] == "progress"] == [
        ("planning", "正在确认本次修改范围"), ("validation", "正在检查正文规范与 Word 排版")]
    assert all(event["revision"] == 7 for event in events)
    assert events[-1]["content"] == "旧正文，日期域必须保留。\n\n清晰的新正文。" and len(checked) == 1
    assert events[-1]["operations"][0]["op"] == "insertAfter"


def test_slow_word_validation_keeps_connection_alive(route_context, monkeypatch):
    from time import sleep
    request, row, _, _, _ = route_context
    original = module._keep_draft_alive
    monkeypatch.setattr(module, "_keep_draft_alive", lambda stream: original(stream, interval=0.005))
    monkeypatch.setattr(module, "_check_word", lambda *args: sleep(0.03))
    response = module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0), stream=True)

    async def collect():
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect())
    assert "\n" in chunks
    events = [json.loads(chunk) for chunk in chunks if chunk.strip()]
    assert events[-1]["type"] == "done"
    validation = next(index for index, chunk in enumerate(chunks) if '"stage": "validation"' in chunk)
    assert "\n" in chunks[validation + 1:]


def test_model_timeout_has_specific_message(route_context, monkeypatch):
    import httpx
    from openai import APITimeoutError
    request, row, _, _, _ = route_context

    async def generate(**kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "http://offline.invalid"))
        yield

    monkeypatch.setattr(module, "stream_section_text", generate)
    events = stream_events(module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0), stream=True))
    assert events[-1]["type"] == "error"
    assert "AI_READ_TIMEOUT_SECONDS" in events[-1]["message"]


@pytest.mark.parametrize("failure", ["internal", "connection", "provider"])
def test_draft_failure_reports_target_and_logs_stack_without_payload(route_context, monkeypatch, caplog, failure):
    import httpx
    from openai import APIConnectionError, APIStatusError
    request, row, _, _, checked = route_context
    upstream_request = httpx.Request("POST", "http://offline.invalid")
    secret = "private-model-payload-must-not-be-logged"
    error = (APIConnectionError(message=secret, request=upstream_request) if failure == "connection" else
             APIStatusError(secret, response=httpx.Response(502, request=upstream_request), body={"secret": secret})
             if failure == "provider" else RuntimeError(secret))

    async def generate(**kwargs):
        yield {"stage": "module", "message": "正在编写 3/31：系统概述"}
        yield {"stage": "module", "message": "正在编写本节内容 1/2"}
        raise error

    monkeypatch.setattr(module, "stream_section_text", generate)
    events = stream_events(module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0), stream=True))
    assert events[-1]["type"] == "error"
    assert "系统概述" in events[-1]["message"]
    assert ("HTTP 502" if failure == "provider" else "模型服务连接中断" if failure == "connection" else "编写服务处理失败") in events[-1]["message"]
    assert secret not in caplog.text and secret not in events[-1]["message"]
    assert type(error).__name__ in caplog.text and "preflight-test" in caplog.text
    assert checked == []


@pytest.mark.parametrize("op", ["replace", "delete"])
def test_word_heading_paragraph_cannot_be_rewritten_by_ai(route_context, monkeypatch, op):
    """Spec 30 §4.3: a Word heading kept as a paragraph is locked; touching it rejects the whole result."""
    request, row, *_ = route_context
    monkeypatch.setattr(module, "_word_heading_ids", lambda _connection, _row, objects: {objects[0]["id"]})
    seen = {}

    def draft(**kwargs):
        block = kwargs["block_scope"]["blocks"][0]
        seen["locked"] = block.get("locked")
        return json.dumps({"operations": [{"op": op, "blockId": block["id"], "content": "改掉标题"}]}, ensure_ascii=False)

    monkeypatch.setattr(module, "draft_section_text", draft)
    with pytest.raises(AppError) as error:
        module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0))
    assert seen["locked"] is True and error.value.status_code == 409 and error.value.code == "AI_SCOPE_VIOLATION"


def test_word_heading_paragraph_is_an_insert_anchor(route_context, monkeypatch):
    request, row, *_ = route_context
    monkeypatch.setattr(module, "_word_heading_ids", lambda _connection, _row, objects: {objects[0]["id"]})
    monkeypatch.setattr(module, "draft_section_text", lambda **kwargs: json.dumps({"operations": [
        {"op": "insertAfter", "blockId": kwargs["block_scope"]["blocks"][0]["id"], "content": "补充正文。"}]}, ensure_ascii=False))
    data = module.draft_section(request, str(row[0]), module.SectionDraftBody(expectedRevision=7, headingIndex=0))["data"]
    assert data["operations"][0]["op"] == "insertAfter" and data["content"].endswith("补充正文。")
