"""Object-bound advice stays local across API queries, selections and sequential AI modules."""

import asyncio
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

import app.document_agent as agent
import app.documents as documents
from app.config import get_settings
from app.db import connect
from app.document_guidance import object_spans, writing_context
from app.document_selection import selection_blocks
from tests.test_document_versions import version_context  # noqa: F401
from tests.test_template_guidance_targets import source  # noqa: F401
from tests.test_writing_guidance import query, patch


@pytest.fixture
def scoped(version_context, monkeypatch):
    client, headers, document, _project, _owner = version_context
    markdown = "# 范围\n\n重复正文。\n\n重复正文。\n\n| 功能 | 说明 |\n| --- | --- |\n| 登录 | 原有说明 |\n\n# 其他\n\n另章正文。"
    template = {**document["formatTemplate"], "importStructure": {"reviewed": True, "blocks": [
        {"id": "h0", "kind": "heading", "headingLevel": 1, "sourceText": "范围"},
        {"id": "general", "kind": "guidance", "headingId": "h0", "text": "保持准确。"},
        {"id": "h1", "kind": "heading", "headingLevel": 1, "sourceText": "其他"},
    ]}}
    with connect() as connection:
        connection.execute("UPDATE documents SET markdown_content=%s,format_template=%s WHERE id=%s",
                           (markdown, json.dumps(template, ensure_ascii=False), document["id"]))
        connection.commit()
    items = _objects(client, document["id"])
    monkeypatch.setattr(documents, "_check_word", lambda *_args, **_kwargs: None)
    return client, headers, document, markdown, items


def save_advice(scoped):
    client, headers, doc, _markdown, items = scoped
    targets = [item for item in items if item["kind"] in {"paragraph", "table"}]
    result = patch(client, headers, doc["id"], 0, [
        {"id": "object:" + item["objectId"], "text": text}
        for item, text in zip(targets, ["第一段只写输入。", "第二段只写输出。", "表格逐行列出验证条件。", "另章只写术语。"])
    ])
    assert result.status_code == 200, result.text
    return targets


def test_object_advice_query_persist_reset_and_foreign_uuid(scoped):
    client, headers, doc, markdown, _items = scoped
    targets = save_advice(scoped)
    assert targets[0]["objectId"] != targets[1]["objectId"]
    for target, expected in zip(targets, ["第一段只写输入。", "第二段只写输出。", "表格逐行列出验证条件。", "另章只写术语。"]):
        result = query(client, headers, doc["id"], revision=1, scope="objects", objectIds=[target["objectId"]])
        assert result.status_code == 200, result.text
        item = result.json()["data"]["groups"][0]["items"][0]
        assert item["text"] == expected and item["targetObjectIds"] == [target["objectId"]]
    result = patch(client, headers, doc["id"], 1, [{"id": "object:" + str(uuid4()), "text": "不能串到其他对象。"}])
    assert result.status_code == 422
    reset = patch(client, headers, doc["id"], 1, [{"id": "object:" + targets[0]["objectId"], "text": None}])
    assert reset.status_code == 200
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["data"]["markdownContent"] == markdown
    history = client.get(f"/api/v1/documents/{doc['id']}/versions/1").json()["data"]
    assert history["formatTemplate"]["blockGuidance"][targets[0]["objectId"]] == "第一段只写输入。"


def test_same_text_selection_and_cross_paragraph_scope_do_not_borrow_advice(scoped):
    client, headers, doc, markdown, _ = scoped
    targets = save_advice(scoped)
    blocks = selection_blocks(markdown)
    for index in [0, 1]:
        selection = {"blockId": blocks[index]["blockId"], "start": 0, "end": 5, "text": "重复正文。"}
        result = query(client, headers, doc["id"], revision=1, scope="selection", selection=selection)
        assert result.status_code == 200, result.text
        selected = result.json()["data"]["groups"][0]["items"]
        assert [item["id"] for item in selected] == ["general", "object:" + targets[index]["objectId"]]
    selection = {"blockId": blocks[0]["blockId"], "endBlockId": blocks[1]["blockId"], "start": 0, "end": 5,
                 "text": "重复正文。\n\n重复正文。"}
    result = query(client, headers, doc["id"], revision=1, scope="selection", selection=selection)
    assert result.status_code == 200, result.text
    assert [item["id"] for item in result.json()["data"]["groups"][0]["items"]] == [
        "general", "object:" + targets[0]["objectId"], "object:" + targets[1]["objectId"]]


def test_selected_ai_receives_only_effective_advice_of_actual_object(scoped, monkeypatch):
    client, headers, doc, markdown, _ = scoped
    targets = save_advice(scoped)
    blocks = selection_blocks(markdown)
    selection = {"blockId": blocks[1]["blockId"], "start": 0, "end": 5, "text": "重复正文。"}
    settings = get_settings().model_copy(update={"ai_base_url": "http://localhost/v1", "ai_model": "offline", "ai_api_key": ""})
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    generated = Mock(return_value="修改正文。")
    monkeypatch.setattr(documents, "draft_section_text", generated)
    response = client.post(f"/api/v1/documents/{doc['id']}/sections/draft", headers=headers,
                           json={"expectedRevision": 1, "scope": "selection", "headingIndex": 0,
                                 "selectedText": selection["text"], "selection": selection})
    assert response.status_code == 200, response.text
    values = generated.call_args.kwargs
    assert values["selected_only"]
    assert [item["id"] for item in values["effective_guidance"]] == ["general", "object:" + targets[1]["objectId"]]
    assert "writing_modules" not in values


@pytest.mark.parametrize("stream", [False, True])
def test_whole_writing_isolated_modules_preserve_table_and_untouched_separators(scoped, monkeypatch, stream):
    client, headers, doc, markdown, _ = scoped
    save_advice(scoped)
    with connect() as connection:
        row = documents._document_row(connection, doc["id"], None, True)
        objects = object_spans(connection, row)
        context = writing_context(row[6], markdown, objects)
    seen = []

    class Agent:
        def __init__(self, content):
            self.content = content

        def invoke(self, _inputs, **_kwargs):
            return {"messages": [AIMessage(content=self.content)]}

        async def astream(self, _inputs, **_kwargs):
            yield "messages", (AIMessageChunk(content=self.content, id="draft"), {"langgraph_node": "model"})

    def factory(**values):
        seen.append(values)
        text = values["existing"].replace("重复正文。", "已生成正文。").replace("原有说明", "已确认说明").replace("另章正文。", "另章完成。")
        return Agent(text), {}, {"ready": True, "templateNotices": [], "recursion_limit": 100}

    monkeypatch.setattr(agent, "_section_agent", factory)
    inputs = {"sequential_document": True, "whole_document": True, "full_markdown": markdown,
              "guidance_context": context, "document_name": "测试", "instruction": "按每段建议写。"}
    if stream:
        async def collect():
            return [item async for item in agent.stream_section_text(**inputs)]
        output = asyncio.run(collect())[-1]
    else:
        output = agent.draft_section_text(**inputs)
    edits = json.loads(output)["edits"]
    assert len(seen) == 4 and len(edits) == 2
    assert [rule["text"] for rule in seen[0]["effective_guidance"]] == ["保持准确。", "第一段只写输入。"]
    assert [rule["text"] for rule in seen[1]["effective_guidance"]] == ["保持准确。", "第二段只写输出。"]
    assert [rule["text"] for rule in seen[2]["effective_guidance"]] == ["保持准确。", "表格逐行列出验证条件。"]
    assert [rule["text"] for rule in seen[3]["effective_guidance"]] == ["另章只写术语。"]
    assert edits[0]["content"] == "已生成正文。\n\n已生成正文。\n\n| 功能 | 说明 |\n| --- | --- |\n| 登录 | 已确认说明 |"
    assert edits[1]["content"] == "另章完成。"


def test_deleted_template_target_is_visible_notice_but_never_reused(scoped, monkeypatch):
    client, headers, doc, markdown, items = scoped
    missing = str(uuid4())
    with connect() as connection:
        row = documents._document_row(connection, doc["id"], None, True)
        template = row[6]
        template["importStructure"]["blocks"].append({"id": "bound", "kind": "guidance", "headingId": "h0",
                                                      "text": "只约束原来的段落。", "targetBlockIds": ["source-p"]})
        template["guidanceTargets"] = {"bound": [missing]}
        connection.execute("UPDATE documents SET format_template=%s WHERE id=%s", (json.dumps(template, ensure_ascii=False), doc["id"]))
        connection.commit()
    affected = query(client, headers, doc["id"], headingIndex=0)
    assert affected.status_code == 200 and affected.json()["data"]["notices"]
    other = query(client, headers, doc["id"], headingIndex=1)
    assert other.status_code == 200, other.text
    assert not any(rule["id"] == "bound" for group in other.json()["data"]["groups"] for rule in group["items"])
    with connect() as connection:
        row = documents._document_row(connection, doc["id"], None, True)
        with pytest.raises(ValueError, match="全文"):
            writing_context(row[6], markdown, object_spans(connection, row))
    settings = get_settings().model_copy(update={"ai_base_url": "http://localhost/v1", "ai_model": "offline", "ai_api_key": ""})
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    model = Mock()
    monkeypatch.setattr(documents, "draft_section_text", model)
    response = client.post(f"/api/v1/documents/{doc['id']}/sections/draft", headers=headers,
                           json={"expectedRevision": 0, "scope": "document"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "GUIDANCE_UNMATCHED"
    model.assert_not_called()


def test_partial_template_target_missing_rejects_affected_selection(scoped):
    client, headers, doc, _markdown, items = scoped
    live_id = items[1]["objectId"]
    with connect() as connection:
        row = documents._document_row(connection, doc["id"], None, True)
        template = row[6]
        template["importStructure"]["blocks"].append({"id": "bound", "kind": "guidance", "headingId": "h0",
                                                      "text": "两段共同约束。", "targetBlockIds": ["a", "b"]})
        template["guidanceTargets"] = {"bound": [live_id, str(uuid4())]}
        connection.execute("UPDATE documents SET format_template=%s WHERE id=%s", (json.dumps(template), doc["id"]))
        connection.commit()
    result = query(client, headers, doc["id"], headingIndex=0)
    assert result.status_code == 422 and result.json()["error"]["code"] == "GUIDANCE_UNMATCHED"
    assert query(client, headers, doc["id"], headingIndex=1).status_code == 200


def _objects(client, document_id):
    """合并后对象列表取自 /objects（原分支的分工接口已统一到 A07）。"""
    data = client.get(f"/api/v1/documents/{document_id}/objects").json()["data"]["objects"]
    return [{"objectId": item["id"], "kind": item["kind"], "text": item["markdown"]} for item in data]


def test_imported_template_targets_bind_to_exact_document_objects(version_context, source):
    from app.files import store_bytes
    client, headers, _doc, project, _ = version_context
    path, structure, guidance, _bodies, _table = source
    stored = store_bytes(name="段落指导模板.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         purpose="template", content=path.read_bytes())
    group = client.post("/api/v1/templates", headers=headers, json={"name": "对象指导模板组", "category": "438C"})
    assert group.status_code == 200, group.text
    created = client.post(f"/api/v1/templates/{group.json()['data']['id']}/documents", headers=headers,
                          json={"name": "对象指导模板", "wordFileId": str(stored.id), "importStructure": structure})
    assert created.status_code == 200, created.text
    template_id = created.json()["data"]["id"]
    documents_created = []
    for name in ["首次创建", "同模板另一份"]:
        response = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                               json={"name": name, "sourceType": "template", "templateDocumentId": template_id})
        assert response.status_code == 200, response.text
        doc = response.json()["data"]
        details = client.get(f"/api/v1/documents/{doc['id']}").json()["data"]
        objects = _objects(client, doc["id"])
        duplicates = [item for item in objects if item["text"] == "重复正文。"]
        bindings = details["formatTemplate"]["guidanceTargets"]
        assert bindings[guidance[0]["id"]] == [duplicates[1]["objectId"]]
        table = next(item for item in objects if item["kind"] == "table")
        listing = next(item for item in objects if item["kind"] == "list_item")
        assert set(bindings[guidance[1]["id"]]) == {table["objectId"], listing["objectId"]}
        result = query(client, headers, doc["id"], headingIndex=0)
        assert result.status_code == 200, result.text
        rules = {item["id"]: item for item in result.json()["data"]["groups"][0]["items"]}
        assert rules[guidance[0]["id"]]["targetObjectIds"] == [duplicates[1]["objectId"]]
        assert rules[guidance[1]["id"]]["targetObjectIds"] == bindings[guidance[1]["id"]]
        documents_created.append({item["objectId"] for item in objects})
    assert not documents_created[0] & documents_created[1]
