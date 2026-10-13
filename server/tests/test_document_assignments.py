"""负责人分工覆盖直接/合并/对象/派生写入，以及成员降级清理。"""

from uuid import UUID, uuid4

import pytest

from app.db import connect
from app.document_objects import check_invariants
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_document_patch import objects, patch, update
from tests.test_documents import document_context  # noqa: F401


def snapshot(client, did):
    response = client.get(f"/api/v1/documents/{did}/assignments")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def assign(client, did, headers, items, **extra):
    data = snapshot(client, did)
    return client.put(f"/api/v1/documents/{did}/assignments", headers=headers, json={
        "expectedRevision": data["revision"], "expectedAssignmentsRevision": data["assignmentsRevision"], "items": items, **extra})


def setup_assignments(team):
    client, did, users = team
    headers = _as(client, "owner")
    data = snapshot(client, did)
    own = next(item for item in data["items"] if item["text"] == "第一段。")
    other = next(item for item in data["items"] if item["text"] == "第二段。")
    response = assign(client, did, headers, [
        {"objectId": own["objectId"], "assigneeId": str(users["editor"])},
        {"objectId": other["objectId"], "assigneeId": str(users["editor2"])},
    ])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["enabled"] is True
    return own, other


def test_assignment_management_cas_scope_and_eligibility(team):
    client, did, users = team
    owner = _as(client, "owner")
    data = snapshot(client, did)
    assert not data["enabled"] and data["assignmentsRevision"] == 0
    assert set(member["id"] for member in data["members"]) >= {str(users["owner"]), str(users["editor"])}
    oid = data["items"][1]["objectId"]
    for uid, object_id, code in [(users["viewer"], oid, "ASSIGNEE_NOT_ELIGIBLE"), (users["editor"], str(uuid4()), "ASSIGNMENT_OBJECT_INVALID")]:
        rejected = assign(client, did, owner, [{"objectId": object_id, "assigneeId": str(uid)}])
        assert rejected.status_code == 422 and rejected.json()["error"]["code"] == code
    ok = assign(client, did, owner, [{"objectId": oid, "assigneeId": str(users["editor"])}])
    assert ok.status_code == 200
    stale = client.put(f"/api/v1/documents/{did}/assignments", headers=owner, json={
        "expectedRevision": 1, "expectedAssignmentsRevision": 0, "items": [], "enabled": False})
    assert stale.status_code == 409
    assert assign(client, did, _as(client, "editor"), []).status_code == 403
    _as(client, "outsider")
    assert client.get(f"/api/v1/documents/{did}/assignments").status_code == 404


@pytest.mark.parametrize("merge", [True, False])
def test_content_permission_is_atomic_and_keeps_identity(team, merge):
    client, did, _ = team
    own, other = setup_assignments(team)
    headers = _as(client, "editor")
    denied = save(client, did, 1, TEXT.replace("第一段。", "允许改。").replace("第二段。", "不允许改。"), headers, merge=merge)
    assert denied.status_code == 403, denied.text
    assert current(client, did)["markdownContent"] == TEXT
    ok = save(client, did, 1, TEXT.replace("第一段。", "我负责的正文。"), headers, merge=merge)
    assert ok.status_code == 200, ok.text
    after = snapshot(client, did)
    assert next(item for item in after["items"] if item["objectId"] == own["objectId"])["text"] == "我负责的正文。"
    assert next(item for item in after["items"] if item["objectId"] == other["objectId"])["canEdit"] is False
    with connect() as connection:
        assert check_invariants(connection, UUID(did))
        assert connection.execute("SELECT count(*) FROM document_versions WHERE document_id=%s", (did,)).fetchone()[0] == 3


def test_patch_forbidden_and_allowed_then_other_writer_merges(team):
    client, did, _ = team
    setup_assignments(team)
    headers = _as(client, "editor")
    parts = objects(client, did)
    own = next(item for item in parts if item["markdown"] == "第一段。")
    other = next(item for item in parts if item["markdown"] == "第二段。")
    bad = patch(client, did, 1, [update(other, "偷改。")], headers, "assigned-denied")
    assert bad.status_code == 403, bad.text
    first = patch(client, did, 1, [update(own, "甲的内容。")], headers, "assigned-first")
    assert first.status_code == 200, first.text
    second = patch(client, did, 1, [update(other, "乙的内容。")], _as(client, "editor2"), "assigned-second")
    assert second.status_code == 200, second.text
    text = current(client, did)["markdownContent"]
    assert "甲的内容。" in text and "乙的内容。" in text


@pytest.mark.parametrize("text", [TEXT + "\n新增。", TEXT.replace("第一段。\n\n", ""), TEXT.replace("第一段。", "第二段。").replace("第三段。", "第一段。"), TEXT.replace("# 1 范围", "# 新标题"), TEXT.replace("第二段。", "第二段。  ")])
def test_structural_and_whitespace_bypass_rejected(team, text):
    client, did, _ = team
    setup_assignments(team)
    response = save(client, did, 1, text, _as(client, "editor"))
    assert response.status_code == 403, response.text
    assert current(client, did)["markdownContent"] == TEXT


def test_owner_structure_and_disable_keep_backward_compatibility(team):
    client, did, _ = team
    setup_assignments(team)
    headers = _as(client, "owner")
    assert save(client, did, 1, TEXT + "\n新增内容。", headers).status_code == 200
    assert assign(client, did, headers, [], enabled=False).status_code == 200
    text = current(client, did)["markdownContent"]
    result = save(client, did, 2, text + "\n自由编写。", _as(client, "editor"))
    assert result.status_code == 200, result.text


def test_member_downgrade_clears_assignments_but_keeps_protection(team):
    client, did, users = team
    setup_assignments(team)
    headers = _as(client, "owner")
    with connect() as connection:
        pid = str(connection.execute("SELECT project_id FROM documents WHERE id=%s", (did,)).fetchone()[0])
    members_data = client.get(f"/api/v1/projects/{pid}/members").json()["data"]
    assigned_member = next(item for item in members_data["items"] if item["userId"] == str(users["editor"]))
    assert assigned_member["assignments"]["blocks"] == 1
    result = client.put(f"/api/v1/projects/{pid}/members/{users['editor']}", headers=headers,
                        json={"role": "viewer", "expectedMembersRevision": members_data["membersRevision"]})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["assignmentsCleared"]["blocks"] == 1
    after = snapshot(client, did)
    assert after["enabled"] and after["assignmentsRevision"] == 2
    assert not any(item["assigneeId"] == str(users["editor"]) for item in after["items"])
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM document_assignment_events WHERE document_id=%s", (did,)).fetchone()[0] == 2


@pytest.mark.parametrize("merge", [True, False])
def test_ai_adoption_and_restore_cannot_bypass_assignments(team, merge):
    client, did, _ = team
    setup_assignments(team)
    headers = _as(client, "editor")
    for path, body in [
        ("sections/insert", {"headingIndex": 1, "mode": "replace", "content": "不属于我的引用段。"}),
        ("edits", {"edits": [{"headingIndex": 1, "content": "全文模式修改引用段。"}]}),
    ]:
        response = client.post(f"/api/v1/documents/{did}/{path}", headers=headers,
                               json={"expectedRevision": 1, "allowMerge": merge, **body})
        assert response.status_code == 403, response.text
        assert current(client, did)["markdownContent"] == TEXT
    response = client.post(f"/api/v1/documents/{did}/versions/0/restore", headers=headers, json={"expectedRevision": 1})
    assert response.status_code == 403, response.text
    assert current(client, did)["markdownContent"] == TEXT


def test_assignment_revocation_during_word_preflight_is_rechecked(team, monkeypatch):
    client, did, _ = team
    own, _ = setup_assignments(team)
    headers = _as(client, "editor")

    def revoke(*_args, **_kwargs):
        with connect() as connection:
            connection.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s", (did, own["objectId"]))
            connection.commit()

    monkeypatch.setattr("app.documents._check_word", revoke)
    response = save(client, did, 1, TEXT.replace("第一段。", "预检期间被撤销权限。"), headers)
    assert response.status_code == 403, response.text
    assert current(client, did)["markdownContent"] == TEXT


def test_conflict_resolution_rechecks_assignment(team):
    client, did, _ = team
    # 先产生真实冲突，然后分工变化；旧冲突不能恢复已撤销的编辑权。
    first = save(client, did, 1, TEXT.replace("第二段。", "别人修改。"), _as(client, "editor2"))
    assert first.status_code == 200
    response = save(client, did, 1, TEXT.replace("第二段。", "我的冲突草稿。"), _as(client, "editor"))
    assert response.status_code == 409
    conflict = response.json()["error"]["details"]
    setup_assignments_for_current = snapshot(client, did)
    owner = _as(client, "owner")
    assert assign(client, did, owner, [], enabled=True).status_code == 200
    item = conflict["conflicts"][0]
    response = client.post(f"/api/v1/documents/{did}/conflicts/{conflict['conflictId']}/resolve", headers=_as(client, "editor"), json={
        "clientSaveKey": "assigned-resolve-denied", "baseDocumentRevision": setup_assignments_for_current["revision"],
        "items": [{"itemIndex": 0, "action": "keep_mine", "theirsRevision": item["theirs"]["revision"]}]})
    assert response.status_code == 403, response.text
    assert "别人修改。" in current(client, did)["markdownContent"]


def test_archived_and_disabled_assignment_targets(team):
    client, did, users = team
    headers = _as(client, "owner")
    data = snapshot(client, did)
    with connect() as connection:
        connection.execute("UPDATE users SET status='disabled' WHERE id=%s", (users["editor"],))
        connection.commit()
    rejected = assign(client, did, headers, [{"objectId": data["items"][1]["objectId"], "assigneeId": str(users["editor"])}])
    assert rejected.status_code == 422
    with connect() as connection:
        connection.execute("UPDATE projects SET archived_at=CURRENT_TIMESTAMP WHERE id=(SELECT project_id FROM documents WHERE id=%s)", (did,))
        connection.commit()
    response = assign(client, did, headers, [], enabled=True)
    assert response.status_code == 409 and response.json()["error"]["code"] == "PROJECT_ARCHIVED"
    after = snapshot(client, did)
    assert not after["canManage"] and all(not item["canEdit"] for item in after["items"])


def test_assigned_table_can_be_written_and_owner_move_keeps_assignment(team):
    client, did, users = team
    owner = _as(client, "owner")
    table = "| 功能 | 说明 |\n| --- | --- |\n| 登录 | 校验账号 |"
    assert save(client, did, 1, TEXT + "\n" + table, owner).status_code == 200
    part = next(item for item in snapshot(client, did)["items"] if item["kind"] == "table")
    assert assign(client, did, owner, [{"objectId": part["objectId"], "assigneeId": str(users["editor"])}]).status_code == 200
    data = current(client, did)
    written = save(client, did, data["revision"], data["markdownContent"].replace("校验账号", "校验账号和密码"), _as(client, "editor"))
    assert written.status_code == 200, written.text
    owner = _as(client, "owner")
    table_object = next(item for item in objects(client, did) if item["kind"] == "table")
    moved = patch(client, did, written.json()["data"]["revision"], [
        {"op": "move", "objectId": table_object["objectId"], "afterObjectId": None}], owner, "owner-moves-table")
    assert moved.status_code == 200, moved.text
    first = snapshot(client, did)["items"][0]
    assert first["objectId"] == part["objectId"] and first["assigneeId"] == str(users["editor"])
