"""A document pins a skill revision without sharing mutable global settings."""

import json

from fastapi.testclient import TestClient
import pytest

import app.documents as documents
from app.config import get_settings
from app.db import connect
from app.identity import create_user
from test_document_selection_api import descriptor
from test_document_versions import version_context, login  # noqa: F401 - pytest fixtures


def create_skill(client, headers, name="本地编写规范"):
    response = client.post("/api/v1/writing-skills", json={
        "name": name, "instructions": "仅使用已经确认的项目事实。", "baseKind": "general",
        "forbiddenTerms": ["未经确认"], "maxChars": 1000,
    }, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def bind(client, headers, document_id, revision, skill=None, *, source_revision=None, mode="selected", extra="本篇要求。"):
    payload = {"expectedRevision": revision, "mode": mode, "documentInstructions": extra}
    if mode == "selected":
        payload.update(skillId=skill["id"], skillRevision=skill["revision"] if source_revision is None else source_revision)
    return client.patch(f"/api/v1/documents/{document_id}/writing-skill", json=payload, headers=headers)


def detail(client, document_id):
    response = client.get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def snapshot(client, document_id):
    return detail(client, document_id)["formatTemplate"].get("writingSkill")


def adopt(client, headers, document_id, revision, scope, content, selection):
    if scope == "section":
        suffix = "sections/insert"
        payload = {"expectedRevision": revision, "headingIndex": 0, "content": content, "mode": "replace", "instruction": "按当前规范编写。"}
    elif scope == "selection":
        suffix = "selection"
        payload = {"expectedRevision": revision, "selection": selection, "content": content, "instruction": "按当前规范编写。"}
    else:
        suffix = "edits"
        payload = {"expectedRevision": revision, "edits": [{"headingIndex": 0, "content": content}], "instruction": "按当前规范编写。"}
    return client.post(f"/api/v1/documents/{document_id}/{suffix}", json=payload, headers=headers)


def test_document_bound_snapshot_is_isolated_and_global_edit_does_not_change_it(version_context):
    client, headers, doc, project, _ = version_context
    skill = create_skill(client, headers)
    second_response = client.post(f"/api/v1/projects/{project}/documents", json={"name": "另一篇文档", "sourceType": "upload", "wordFileId": doc["originalDocxFileId"]}, headers=headers)
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()["data"]
    before = detail(client, doc["id"])
    changed = bind(client, headers, doc["id"], 0, skill)
    assert changed.status_code == 200, changed.text
    after = detail(client, doc["id"])
    assert after["revision"] == 1
    for field in ("markdownContent", "outline", "status", "originalDocxFileId"):
        assert after[field] == before[field]
    assert {key: value for key, value in after["formatTemplate"].items() if key != "writingSkill"} == before["formatTemplate"]
    bound = snapshot(client, doc["id"])
    assert bound["mode"] == "selected" and bound["id"] == skill["id"] and bound["revision"] == 0
    assert bound["instructions"] == skill["instructions"] and bound["documentInstructions"] == "本篇要求。"
    assert snapshot(client, second["id"]) is None
    global_edit = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 0, "instructions": "全局新规则。", "forbiddenTerms": ["另一禁用词"]}, headers=headers)
    assert global_edit.status_code == 200, global_edit.text
    assert snapshot(client, doc["id"]) == bound
    assert snapshot(client, second["id"]) is None
    local_edit = bind(client, headers, doc["id"], 1, skill, extra="只用于这一篇。")
    assert local_edit.status_code == 200, local_edit.text
    assert snapshot(client, doc["id"])["instructions"] == skill["instructions"]
    assert client.get(f"/api/v1/writing-skills/{skill['id']}").json()["data"]["instructions"] == "全局新规则。"


def test_refresh_checks_both_document_and_global_skill_revisions(version_context):
    client, headers, doc, _, _ = version_context
    skill = create_skill(client, headers)
    assert bind(client, headers, doc["id"], 0, skill).status_code == 200
    updated = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 0, "instructions": "版本一要求。"}, headers=headers).json()["data"]
    updated_again = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 1, "instructions": "版本二要求。"}, headers=headers).json()["data"]
    before = detail(client, doc["id"])
    stale_source = bind(client, headers, doc["id"], 1, updated)
    assert stale_source.status_code == 409 and stale_source.json()["error"]["code"] == "SKILL_REVISION_CONFLICT"
    assert detail(client, doc["id"]) == before
    refreshed = bind(client, headers, doc["id"], 1, updated_again)
    assert refreshed.status_code == 200, refreshed.text
    assert snapshot(client, doc["id"])["revision"] == 2
    assert snapshot(client, doc["id"])["instructions"] == "版本二要求。"
    stale_document = bind(client, headers, doc["id"], 1, updated_again, extra="不能覆盖。")
    assert stale_document.status_code == 409 and stale_document.json()["error"]["code"] == "REVISION_CONFLICT"
    assert snapshot(client, doc["id"])["documentInstructions"] == "本篇要求。"


def test_disabled_skill_rejects_new_binding_but_pinned_version_keeps_local_requirements(version_context):
    client, headers, doc, project, _ = version_context
    skill = create_skill(client, headers)
    assert bind(client, headers, doc["id"], 0, skill).status_code == 200
    disabled_response = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 0, "status": "disabled"}, headers=headers)
    assert disabled_response.status_code == 200, disabled_response.text
    disabled = disabled_response.json()["data"]
    created = client.post(f"/api/v1/projects/{project}/documents", json={"name": "未绑定文档", "sourceType": "upload", "wordFileId": doc["originalDocxFileId"]}, headers=headers).json()["data"]
    for source in (skill, disabled):
        denied = bind(client, headers, created["id"], 0, source)
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "SKILL_DISABLED"
    assert detail(client, created["id"])["revision"] == 0 and snapshot(client, created["id"]) is None
    local = bind(client, headers, doc["id"], 1, skill, extra="停用后继续使用本篇已确认要求。")
    assert local.status_code == 200, local.text
    assert snapshot(client, doc["id"])["revision"] == 0
    assert snapshot(client, doc["id"])["documentInstructions"] == "停用后继续使用本篇已确认要求。"


@pytest.mark.parametrize("scope", ["section", "selection", "document"])
def test_binding_new_revision_rejects_old_ai_adoption_and_preserves_body(version_context, scope):
    client, headers, doc, _, _ = version_context
    selection = descriptor(client, doc["id"])
    skill = create_skill(client, headers)
    assert bind(client, headers, doc["id"], 0, skill).status_code == 200
    before = detail(client, doc["id"])
    stale = adopt(client, headers, doc["id"], 0, scope, "已经核对的正文。", selection)
    assert stale.status_code == 409, stale.text
    assert detail(client, doc["id"]) == before
    accepted = adopt(client, headers, doc["id"], 1, scope, "已经核对的正文。", selection)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["revision"] == 2
    assert "已经核对的正文。" in accepted.json()["data"]["markdownContent"]


@pytest.mark.parametrize("scope", ["section", "selection", "document"])
def test_bound_forbidden_terms_apply_to_every_ai_adoption_and_cannot_be_split_by_markdown(version_context, scope):
    client, headers, doc, _, _ = version_context
    selection = descriptor(client, doc["id"])
    skill = create_skill(client, headers)
    assert bind(client, headers, doc["id"], 0, skill).status_code == 200
    before = detail(client, doc["id"])
    content = "未经确认的结论。" if scope == "selection" else "未经**确认**的结论。"
    rejected = adopt(client, headers, doc["id"], 1, scope, content, selection)
    assert rejected.status_code == 422, rejected.text
    assert "禁用" in rejected.json()["error"]["message"]
    assert detail(client, doc["id"]) == before
    assert client.get(f"/api/v1/documents/{doc['id']}/memory").json()["data"]["items"] == []
    assert [item["revision"] for item in client.get(f"/api/v1/documents/{doc['id']}/versions").json()["data"]["items"]] == [1, 0]


def test_restore_version_restores_pinned_skill_and_document_requirements(version_context):
    client, headers, doc, _, _ = version_context
    skill = create_skill(client, headers)
    assert bind(client, headers, doc["id"], 0, skill, extra="旧版本篇要求。").status_code == 200
    original = snapshot(client, doc["id"])
    assert bind(client, headers, doc["id"], 1, skill, extra="新版本篇要求。").status_code == 200
    assert bind(client, headers, doc["id"], 2, mode="none", extra="当前不使用规范。").status_code == 200
    assert snapshot(client, doc["id"])["mode"] == "none"
    disabled = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 0, "instructions": "全局已改变。", "status": "disabled"}, headers=headers)
    assert disabled.status_code == 200
    restored = client.post(f"/api/v1/documents/{doc['id']}/versions/1/restore", json={"expectedRevision": 3}, headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["revision"] == 4
    assert snapshot(client, doc["id"]) == original
    assert restored.json()["data"]["formatTemplate"]["writingSkill"] == original
    historical = client.get(f"/api/v1/documents/{doc['id']}/versions/1").json()["data"]
    assert historical["formatTemplate"]["writingSkill"] == original
    assert client.get(f"/api/v1/documents/{doc['id']}/versions").json()["data"]["items"][0]["kind"] == "restore"


def test_document_binding_permission_csrf_and_forged_snapshot_are_rejected(version_context):
    client, headers, doc, _, _ = version_context
    skill = create_skill(client, headers)
    payload = {"expectedRevision": 0, "mode": "selected", "skillId": skill["id"], "skillRevision": 0, "documentInstructions": "要求。"}
    url = f"/api/v1/documents/{doc['id']}/writing-skill"
    assert client.patch(url, json=payload).status_code == 403
    assert client.patch(url, json={**payload, "instructions": "伪造规则。"}, headers=headers).status_code == 422
    other = TestClient(client.app)
    assert other.patch(url, json=payload, headers=login(other, "version-other")).status_code == 404
    auditor = create_user(account="skill-auditor", name="只读", password="version-test-password", roles=["auditor"])
    # S1：非项目成员一律 404（上面的 version-other）；I6：auditor 是全局只读审计，看得到文档但不能写 → 403
    # （测试工程师口径，待用户确认）；成为只读成员后同样 403。
    assert other.patch(url, json=payload, headers=login(other, "skill-auditor")).status_code == 403
    with connect() as connection:
        connection.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (%s, %s, 'viewer')", (doc["projectId"], auditor))
        connection.commit()
    assert other.patch(url, json=payload, headers=login(other, "skill-auditor")).status_code == 403
    assert detail(client, doc["id"])["revision"] == 0 and snapshot(client, doc["id"]) is None


def test_settings_version_failure_rolls_back_binding_and_does_not_check_word(version_context, monkeypatch):
    client, headers, doc, _, _ = version_context
    skill = create_skill(client, headers)
    before = detail(client, doc["id"])
    record = documents.record_current_version
    def fail_after_save(connection, document_id, **kwargs):
        if kwargs.get("expected_revision") == 1:
            raise RuntimeError("isolated skill version write failure")
        return record(connection, document_id, **kwargs)
    def no_word_check(*_args, **_kwargs):
        raise AssertionError("writing-skill metadata must not rewrite Word")
    monkeypatch.setattr(documents, "record_current_version", fail_after_save)
    monkeypatch.setattr(documents, "_check_word", no_word_check)
    result = bind(client, headers, doc["id"], 0, skill)
    assert result.status_code == 500
    assert detail(client, doc["id"]) == before
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS total FROM document_versions WHERE document_id=%s", (doc["id"],)).fetchone()["total"] == 1


@pytest.mark.parametrize("constraint", ["maxChars", "forbiddenTerms"])
def test_append_checks_complete_result_and_draft_only_offers_replace(version_context, monkeypatch, constraint):
    client, headers, doc, _, _ = version_context
    existing = "已确认" * 24 if constraint == "maxChars" else "未经确认的旧正文。"
    content = "确认内容" * 10
    prepared = client.patch(f"/api/v1/documents/{doc['id']}/content", json={"expectedRevision": 0, "markdownContent": "# 范围\n\n" + existing}, headers=headers)
    assert prepared.status_code == 200, prepared.text
    skill = create_skill(client, headers)
    limited = client.patch(f"/api/v1/writing-skills/{skill['id']}", json={"expectedRevision": 0, "maxChars": 100}, headers=headers)
    assert limited.status_code == 200, limited.text
    assert bind(client, headers, doc["id"], 1, limited.json()["data"]).status_code == 200
    before = detail(client, doc["id"])
    settings = get_settings().model_copy(update={"ai_base_url": "http://localhost/v1", "ai_model": "offline", "ai_api_key": ""})
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    # Spec 30 §4: an existing section is edited by block operations; rewrite its only paragraph.
    monkeypatch.setattr(documents, "draft_section_text", lambda **kwargs: json.dumps({"operations": [
        {"op": "replace", "blockId": kwargs["block_scope"]["blocks"][0]["id"], "content": content}]}, ensure_ascii=False))
    drafted = client.post(f"/api/v1/documents/{doc['id']}/sections/draft", json={"expectedRevision": 2, "headingIndex": 0}, headers=headers)
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["data"]["allowedModes"] == ["replace"] and drafted.json()["data"]["content"] == content
    append = client.post(f"/api/v1/documents/{doc['id']}/sections/insert", json={"expectedRevision": 2, "headingIndex": 0, "content": content, "mode": "append"}, headers=headers)
    assert append.status_code == 422, append.text
    assert ("上限" if constraint == "maxChars" else "禁用") in append.json()["error"]["message"]
    assert detail(client, doc["id"]) == before
    assert client.get(f"/api/v1/documents/{doc['id']}/memory").json()["data"]["items"] == []
    replaced = client.post(f"/api/v1/documents/{doc['id']}/sections/insert", json={"expectedRevision": 2, "headingIndex": 0, "content": content, "mode": "replace"}, headers=headers)
    assert replaced.status_code == 200, replaced.text
    appended = client.post(f"/api/v1/documents/{doc['id']}/sections/insert", json={"expectedRevision": 3, "headingIndex": 0, "content": content, "mode": "append"}, headers=headers)
    assert appended.status_code == 200, appended.text
    assert appended.json()["data"]["markdownContent"].count(content) == 2


@pytest.mark.parametrize("mode", ["selected", "auto", "none"])
def test_explicit_document_skill_blocks_legacy_memory_save_but_allows_manual_edit(version_context, mode):
    client, headers, doc, _, _ = version_context
    skill = create_skill(client, headers) if mode == "selected" else None
    assert bind(client, headers, doc["id"], 0, skill, mode=mode).status_code == 200
    before = detail(client, doc["id"])
    payload = {"expectedRevision": 1, "markdownContent": "# 范围\n\n手工已确认正文。", "memory": {"instruction": "旧入口不能绕过规范。", "scope": "selection", "target": "范围"}}
    rejected = client.patch(f"/api/v1/documents/{doc['id']}/content", json=payload, headers=headers)
    assert rejected.status_code == 422 and "采纳接口" in rejected.json()["error"]["message"]
    assert detail(client, doc["id"]) == before
    assert client.get(f"/api/v1/documents/{doc['id']}/memory").json()["data"]["items"] == []
    payload.pop("memory")
    manual = client.patch(f"/api/v1/documents/{doc['id']}/content", json=payload, headers=headers)
    assert manual.status_code == 200, manual.text
    assert manual.json()["data"]["revision"] == 2
    assert snapshot(client, doc["id"]) == before["formatTemplate"]["writingSkill"]


def test_unbound_document_keeps_legacy_memory_save_compatibility(version_context):
    client, headers, doc, _, _ = version_context
    assert snapshot(client, doc["id"]) is None
    payload = {"expectedRevision": 0, "markdownContent": "# 范围\n\n已确认正文。", "memory": {"instruction": "保留已有术语。", "scope": "selection", "target": "范围"}}
    accepted = client.patch(f"/api/v1/documents/{doc['id']}/content", json=payload, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["revision"] == 1
    assert client.get(f"/api/v1/documents/{doc['id']}/memory").json()["data"]["items"][0]["instruction"] == payload["memory"]["instruction"]
