"""S7：块级补丁保存 POST /objects/patch（01 §4.2；§8.2 用例 12–14、23 的 2b 部分）与预览对象 ID。"""

import threading
from uuid import UUID

import pytest

from app.db import connect
from app.document_objects import check_invariants
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401


def objects(client, did):
    items = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    return [{**item, "objectId": item["id"]} for item in items]


def by_text(items, text):
    return next(item for item in items if item["markdown"] == text)


def patch(client, did, base, ops, headers, key):
    return client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers,
                       json={"clientSaveKey": key, "baseDocumentRevision": base, "ops": ops})


def update(item, text):
    return {"op": "update", "objectId": item["objectId"], "baseRevision": item["revision"], "markdown": text}


@pytest.mark.parametrize("order", ["AB", "BA"])
def test_patch_different_paragraphs_merge(team, order):
    # 用例 12（2b）
    client, did, users = team
    items = objects(client, did)
    p1, p3 = by_text(items, "第一段。"), by_text(items, "第三段。")
    ops = {"editor": [update(p1, "A 改第一段。")], "editor2": [update(p3, "B 改第三段。")]}
    sequence = ["editor", "editor2"] if order == "AB" else ["editor2", "editor"]
    first = patch(client, did, 1, ops[sequence[0]], _as(client, sequence[0]), f"patch-{order}-1")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["merged"] is False
    second = patch(client, did, 1, ops[sequence[1]], _as(client, sequence[1]), f"patch-{order}-2")
    assert second.status_code == 200, second.text
    data = second.json()["data"]
    assert data["merged"] is True and data["documentRevision"] == 3
    assert data["changedBy"][0]["name"] == f"成员{sequence[0]}"
    text = current(client, did)["markdownContent"]
    assert "A 改第一段。" in text and "B 改第三段。" in text
    after = objects(client, did)
    assert by_text(after, "A 改第一段。")["objectId"] == p1["objectId"]
    assert by_text(after, "B 改第三段。")["objectId"] == p3["objectId"]
    with connect() as connection:
        assert check_invariants(connection, UUID(did))


def test_patch_same_paragraph_conflicts_and_resolves(team):
    # 用例 13（2b）：409 带三方内容与对方姓名；文档未被 B 改动；appliableOps 只含无冲突 op
    client, did, _ = team
    items = objects(client, did)
    p2, ref = by_text(items, "第二段。"), by_text(items, "引用段。")
    assert patch(client, did, 1, [update(p2, "A 的第二段。")], _as(client, "editor"), "patch-a-2").status_code == 200
    b = _as(client, "editor2")
    rejected = patch(client, did, 1, [update(p2, "B 的第二段。"), update(ref, "B 改引用段。")], b, "patch-b-2")
    assert rejected.status_code == 409, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "OBJECT_CONFLICT"
    [item] = error["details"]["conflicts"]
    assert item["reason"] == "MODIFIED" and item["objectIds"] == [p2["objectId"]]
    assert item["base"]["markdown"] == "第二段。" and item["theirs"]["markdown"] == "A 的第二段。"
    assert item["theirs"]["updatedBy"]["name"] == "成员editor" and item["mine"]["markdown"] == "B 的第二段。"
    assert error["details"]["appliableOps"] == [1]
    assert "B 改引用段。" in error["details"]["appliableMarkdown"] and "A 的第二段。" in error["details"]["appliableMarkdown"]
    assert "B 的" not in current(client, did)["markdownContent"]
    # 无冲突部分用新键再提交（整篇入口，appliableMarkdown）
    partial = save(client, did, error["details"]["appliableBaseRevision"], error["details"]["appliableMarkdown"], b, key="patch-b-partial")
    assert partial.status_code == 200 and "B 改引用段。" in partial.json()["data"]["markdownContent"]
    # 冲突在面板里“保留我的”
    cid = error["details"]["conflictId"]
    resolved = client.post(f"/api/v1/documents/{did}/conflicts/{cid}/resolve", headers=b, json={
        "clientSaveKey": "patch-b-resolve", "baseDocumentRevision": partial.json()["data"]["revision"],
        "items": [{"itemIndex": 0, "action": "keep_mine", "theirsRevision": item["theirs"]["revision"]}]})
    assert resolved.status_code == 200, resolved.text
    assert "B 的第二段。" in resolved.json()["data"]["markdownContent"]


def test_patch_delete_insert_move_and_ids(team):
    # 用例 23（2b）：插入/移动/复制相同文字后，原对象 ID 仍指向原段
    client, did, _ = team
    headers = _as(client, "editor")
    items = objects(client, did)
    p1, p2, p3 = by_text(items, "第一段。"), by_text(items, "第二段。"), by_text(items, "第三段。")
    response = patch(client, did, 1, [
        {"op": "insert", "tempId": "t1", "afterObjectId": p1["objectId"], "markdown": "第一段。"},
        {"op": "move", "objectId": p3["objectId"], "afterObjectId": None},
        {"op": "delete", "objectId": p2["objectId"], "baseRevision": p2["revision"]},
    ], headers, "patch-structure-1")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert current(client, did)["markdownContent"].startswith("第三段。\n\n# 1 范围\n\n第一段。\n\n第一段。\n\n# 2 引用")
    inserted = next(entry for entry in data["applied"] if entry.get("tempId") == "t1")
    after = objects(client, did)
    copies = [item for item in after if item["markdown"] == "第一段。"]
    assert len(copies) == 2 and copies[0]["objectId"] == p1["objectId"] and copies[1]["objectId"] == inserted["objectId"]
    assert after[0]["objectId"] == p3["objectId"]
    assert all(item["objectId"] != p2["objectId"] for item in after)
    # 同键重放：不重复插入
    again = patch(client, did, 1, [
        {"op": "insert", "tempId": "t1", "afterObjectId": p1["objectId"], "markdown": "第一段。"},
        {"op": "move", "objectId": p3["objectId"], "afterObjectId": None},
        {"op": "delete", "objectId": p2["objectId"], "baseRevision": p2["revision"]},
    ], headers, "patch-structure-1")
    assert again.status_code == 200 and again.json()["data"]["documentRevision"] == data["documentRevision"]
    assert current(client, did)["markdownContent"].count("第一段。") == 2
    # 同一对象出现两次 → 422；不属于文档的 ID → 422
    duplicate = patch(client, did, 2, [update(after[1], "x"), {"op": "delete", "objectId": after[1]["objectId"], "baseRevision": 0}],
                      headers, "patch-dup-1")
    assert duplicate.status_code == 422
    foreign = patch(client, did, 2, [{"op": "update", "objectId": "00000000-0000-0000-0000-000000000000", "baseRevision": 0,
                                      "markdown": "x"}], headers, "patch-foreign-1")
    assert foreign.status_code == 422


def test_patch_deleted_by_other_is_conflict(team):
    client, did, _ = team
    items = objects(client, did)
    p2 = by_text(items, "第二段。")
    assert save(client, did, 1, TEXT.replace("第二段。\n\n", ""), _as(client, "editor")).status_code == 200
    rejected = patch(client, did, 1, [update(p2, "B 改了被删的段。")], _as(client, "editor2"), "patch-deleted-1")
    assert rejected.status_code == 409
    [item] = rejected.json()["error"]["details"]["conflicts"]
    assert item["reason"] == "DELETED" and item["theirs"]["deleted"] is True
    assert item["theirs"]["deletedBy"]["name"] == "成员editor"


def test_patch_viewer_and_outsider_denied(team):
    client, did, _ = team
    item = by_text(objects(client, did), "第一段。")
    assert patch(client, did, 1, [update(item, "x")], _as(client, "viewer"), "patch-viewer-1").status_code == 403
    assert patch(client, did, 1, [update(item, "x")], _as(client, "outsider"), "patch-outsider-1").status_code == 404


@pytest.mark.parametrize("same", [False, True])
def test_patch_barrier_no_lost_update(team, monkeypatch, same):
    # 用例 14（2b）：两请求读同一快照后再提交
    client, did, _ = team
    items = objects(client, did)
    targets = (by_text(items, "第一段。"), by_text(items, "第一段。" if same else "第三段。"))
    barrier = threading.Barrier(2, timeout=10)

    def slow_check(*args, **kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    monkeypatch.setattr("app.documents._check_word", slow_check)
    from fastapi.testclient import TestClient
    from app.main import create_app
    results = {}

    def run(role, target, text):
        with TestClient(create_app()) as own:
            headers = _as(own, role)
            results[role] = patch(own, did, 1, [update(target, text)], headers, f"barrier-{role}-{same}")

    threads = [threading.Thread(target=run, args=("editor", targets[0], "A 并发。")),
               threading.Thread(target=run, args=("editor2", targets[1], "B 并发。"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    codes = sorted(response.status_code for response in results.values())
    text = current(client, did)["markdownContent"]
    if same:
        assert codes == [200, 409]
        assert ("A 并发。" in text) != ("B 并发。" in text)
    else:
        assert codes == [200, 200]
        assert "A 并发。" in text and "B 并发。" in text


def test_preview_carries_object_ids(team):
    client, did, _ = team
    preview = client.get(f"/api/v1/documents/{did}/preview").json()["data"]
    if not preview["objectModel"]:
        pytest.skip("该文档的样式预览与对象不能一一对应，前端走整篇保存")
    ids = {item["objectId"] for item in objects(client, did)}
    import re
    found = set(re.findall(r'data-object-id="([^"]+)"', preview["html"]))
    assert found == ids


def test_patch_atomic_conflict_has_no_appliable_parts(team):
    # 审查 S7：atomic=true 整体拒绝，409 不给可部分应用的 op/全文
    client, did, _ = team
    items = objects(client, did)
    p2, ref = by_text(items, "第二段。"), by_text(items, "引用段。")
    assert patch(client, did, 1, [update(p2, "A 的第二段。")], _as(client, "editor"), "atomic-a-1").status_code == 200
    rejected = client.post(f"/api/v1/documents/{did}/objects/patch", headers=_as(client, "editor2"), json={
        "clientSaveKey": "atomic-b-1", "baseDocumentRevision": 1, "atomic": True,
        "ops": [update(p2, "B 的第二段。"), update(ref, "B 改引用段。")]})
    assert rejected.status_code == 409, rejected.text
    details = rejected.json()["error"]["details"]
    assert details["conflictId"] and details["conflicts"]
    assert not {"appliableOps", "appliableMarkdown", "appliableBaseRevision"} & set(details)
    assert "B 改引用段。" not in current(client, did)["markdownContent"]


def test_patch_rejects_word_body_style_headings(team, monkeypatch):
    """验收 40 P2-4：Word 里套正文样式的标题段落按块保存时被拒（403 HEADING_LOCKED），其他段落照常保存。"""
    client, did, users = team
    items = objects(client, did)
    p1, p3 = by_text(items, "第一段。"), by_text(items, "第三段。")
    import app.fast_preview as fast_preview
    # 复验 P2-1：按块保存改用原稿标题表（43，与预览 data-word-heading 同一判断），只传本次涉及的对象
    seen = []
    def levels(connection, document_id, rows):
        seen.append({str(item["id"]) for item in rows})
        return {str(item["id"]): 2 for item in rows if str(item["id"]) == p1["objectId"]}
    monkeypatch.setattr(fast_preview, "word_heading_levels", levels)
    # QA 复验 16 P2：保存成功后后台预热（warm_after_commit → 预览块）也会调 word_heading_levels（整篇），与本断言竞争；本用例不需要预热
    monkeypatch.setattr(fast_preview, "warm_after_commit", lambda *args, **kwargs: None)
    headers = _as(client, "editor")
    rejected = patch(client, did, 1, [update(p1, "改了标题段。")], headers, "word-heading-1")
    assert rejected.status_code == 403, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "HEADING_LOCKED" and error["details"]["objectIds"] == [p1["objectId"]]
    assert "改了标题段。" not in current(client, did)["markdownContent"]
    deleting = client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers, json={
        "clientSaveKey": "word-heading-2", "baseDocumentRevision": 1, "ops": [{"op": "delete", "objectId": p1["objectId"], "baseRevision": p1["revision"]}]})
    assert deleting.status_code == 403, deleting.text
    assert patch(client, did, 1, [update(p3, "改第三段。")], headers, "word-heading-3").status_code == 200
    assert seen and all(len(ids) == 1 for ids in seen)  # 只查涉及的对象，不扫整篇
