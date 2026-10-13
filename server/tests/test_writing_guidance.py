"""Effective template advice is local, versioned and tied to proven editing scope."""
import json
from unittest.mock import Mock

import pytest
from tests.block_ops_helper import section_answer

import app.documents as documents
from app.config import get_settings
from app.db import connect
from app.document_selection import selection_blocks
from test_document_versions import version_context, login  # noqa: F401


def seed_guidance(doc, *, multiple=False):
    blocks = [
        {"id": "scope", "kind": "heading", "headingLevel": 1, "sourceText": "范围"},
        {"id": "scope-guide", "kind": "guidance", "headingId": "scope", "text": "说明软件边界，不编造事实。"},
    ]
    markdown = "# 范围\n\n原始正文。"
    if multiple:
        blocks.extend([
            {"id": "child", "kind": "heading", "headingLevel": 2, "sourceText": "功能"},
            {"id": "child-guide", "kind": "guidance", "headingId": "child", "text": "本模块逐条列出功能。"},
            {"id": "other", "kind": "heading", "headingLevel": 1, "sourceText": "其他"},
            {"id": "other-guide", "kind": "guidance", "headingId": "other", "text": "其他模块只写接口。"},
        ])
        markdown += "\n\n## 功能\n\n重复正文。\n\n重复正文。\n\n# 其他\n\n重复正文。"
    template = {**doc["formatTemplate"], "importStructure": {"reviewed": True, "blocks": blocks}}
    with connect() as connection:
        connection.execute("UPDATE documents SET format_template=%s, markdown_content=%s WHERE id=%s",
                           [json.dumps(template, ensure_ascii=False), markdown, doc["id"]])
        connection.commit()
    return template, markdown


def query(client, headers, did, revision=0, **kwargs):
    return client.post(f"/api/v1/documents/{did}/writing-guidance/query", headers=headers,
                       json={"expectedRevision": revision, "scope": "section", "headingIndex": 0, **kwargs})


def patch(client, headers, did, revision, items):
    return client.patch(f"/api/v1/documents/{did}/writing-guidance", headers=headers,
                        json={"expectedRevision": revision, "items": items})


def test_local_advice_is_durable_versioned_and_resettable_without_changing_template(version_context):
    client, headers, doc, project, _ = version_context
    original, _ = seed_guidance(doc)
    second = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                         json={"name": "另一篇", "sourceType": "upload", "wordFileId": doc["originalDocxFileId"]}).json()["data"]
    seed_guidance(second)
    before = query(client, headers, doc["id"]).json()["data"]["groups"][0]["items"][0]
    assert before["text"] == before["sourceText"] and not before["overridden"]
    result = patch(client, headers, doc["id"], 0, [{"id": "scope-guide", "text": "本篇改为先说明使用者，再列软件边界。"}])
    assert result.status_code == 200, result.text
    assert result.json()["data"]["revision"] == 1
    assert result.json()["data"]["formatTemplate"]["importStructure"] == original["importStructure"]
    current = query(client, headers, doc["id"], 1).json()["data"]["groups"][0]["items"][0]
    assert current["overridden"] and current["sourceText"] == before["text"]
    assert current["text"].startswith("本篇改为")
    assert query(client, headers, second["id"]).json()["data"]["groups"][0]["items"][0] == before
    assert query(client, headers, doc["id"], 0).status_code == 409
    assert patch(client, headers, doc["id"], 0, [{"id": "scope-guide", "text": "旧版本修改。"}]).status_code == 409
    history = client.get(f"/api/v1/documents/{doc['id']}/versions/1").json()["data"]
    assert history["formatTemplate"]["writingGuidance"]["scope-guide"] == current["text"]
    restored = patch(client, headers, doc["id"], 1, [{"id": "scope-guide", "text": None}])
    assert restored.status_code == 200
    assert query(client, headers, doc["id"], 2).json()["data"]["groups"][0]["items"][0] == before
    same = patch(client, headers, doc["id"], 2, [{"id": "scope-guide", "text": None}])
    assert same.json()["data"]["revision"] == 2
    restore = client.post(f"/api/v1/documents/{doc['id']}/versions/1/restore", headers=headers,
                          json={"expectedRevision": 2})
    assert restore.status_code == 200, restore.text
    assert query(client, headers, doc["id"], 3).json()["data"]["groups"][0]["items"][0] == current


def test_selection_scope_uses_actual_anchor_not_repeated_text_or_client_heading(version_context, monkeypatch):
    client, headers, doc, _, _ = version_context
    _, markdown = seed_guidance(doc, multiple=True)
    blocks = selection_blocks(markdown)
    selection = {"blockId": blocks[2]["blockId"], "start": 0, "end": 5, "text": "重复正文。"}
    result = query(client, headers, doc["id"], scope="selection", headingIndex=2, selection=selection)
    assert result.status_code == 200, result.text
    group = result.json()["data"]["groups"][0]
    assert group["headingIndex"] == 1 and group["headingPath"] == "范围 / 功能"
    assert [item["id"] for item in group["items"]] == ["scope-guide", "child-guide"]
    model = Mock()
    monkeypatch.setattr(documents, "draft_section_text", model)
    response = client.post(f"/api/v1/documents/{doc['id']}/sections/draft", headers=headers,
                          json={"expectedRevision": 0, "scope": "selection", "headingIndex": 2,
                                "selectedText": selection["text"], "selection": selection})
    assert response.status_code == 422 and response.json()["error"]["code"] == "GUIDANCE_SCOPE_MISMATCH"
    model.assert_not_called()
    full = query(client, headers, doc["id"], scope="document", headingIndex=None).json()["data"]["groups"]
    assert [[item["id"] for item in group["items"]] for group in full] == [
        ["scope-guide"], ["scope-guide", "child-guide"], ["other-guide"]]


@pytest.mark.parametrize("items", [[{"id": "foreign", "text": "未知规则"}],
                                   [{"id": "scope-guide", "text": "   "}],
                                   [{"id": "scope-guide", "text": "长" * 20001}],
                                   [{"id": "scope-guide", "text": "一"}, {"id": "scope-guide", "text": "二"}]])
def test_invalid_advice_does_not_change_document(version_context, items):
    client, headers, doc, _, _ = version_context
    seed_guidance(doc)
    assert patch(client, headers, doc["id"], 0, items).status_code == 422
    assert query(client, headers, doc["id"]).json()["data"]["revision"] == 0


def test_foreign_user_cannot_read_or_edit_advice(version_context):
    client, headers, doc, _, _ = version_context
    seed_guidance(doc)
    foreign = login(client, "version-other")
    assert query(client, foreign, doc["id"]).status_code == 404
    assert patch(client, foreign, doc["id"], 0, [{"id": "scope-guide", "text": "禁止写入。"}]).status_code == 404


def test_draft_uses_persisted_effective_advice(version_context, monkeypatch):
    client, headers, doc, _, _ = version_context
    seed_guidance(doc)
    patch(client, headers, doc["id"], 0, [{"id": "scope-guide", "text": "当前本篇有效要求。"}])
    settings = get_settings().model_copy(update={"ai_base_url": "http://localhost/v1", "ai_model": "offline", "ai_api_key": ""})
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    model = Mock(side_effect=lambda **kwargs: section_answer(kwargs, "原始正文。"))
    monkeypatch.setattr(documents, "draft_section_text", model)
    response = client.post(f"/api/v1/documents/{doc['id']}/sections/draft", headers=headers,
                          json={"expectedRevision": 1, "scope": "section", "headingIndex": 0})
    assert response.status_code == 200, response.text
    assert model.call_args.kwargs["guidance_overrides"] == {"scope-guide": "当前本篇有效要求。"}


def test_partial_whole_preview_does_not_require_images_that_have_not_streamed_yet():
    row = [None] * 16
    row[2], row[6], row[7], row[15] = "一般文档", {}, "# 模块\n\n正文。\n\n![图片](image.png)", ""
    partial = '{"edits":[{"headingIndex":0,"content":"正在生成正文。"}]}'
    assert documents._parse_edits(partial, row, complete=False)[0].content == "正在生成正文。"
    with pytest.raises(documents.DraftFormatError, match="图片"):
        documents._parse_edits(partial, row, complete=True)
