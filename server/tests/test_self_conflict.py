"""QA 复验 14：同一页面先后发出的保存不能自冲突（Ctrl+S 后又输入再关页）；已留下的自冲突在重开时自动处理。"""

import threading

import pytest

from app.db import connect
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_document_patch import by_text, objects
from tests.test_documents import document_context  # noqa: F401


def close_patch(client, did, base, item, text, headers, keys=None, key="close-save-1"):
    body = {"clientSaveKey": key, "baseDocumentRevision": base, "atomic": True,
            "ops": [{"op": "update", "objectId": item["objectId"], "baseRevision": item["revision"], "markdown": text}]}
    if keys is not None:
        body["afterSaveKeys"] = keys
    return client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers, json=body)


def conflicts(did):
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id, status, actor_id FROM document_conflicts WHERE document_id=%s ORDER BY created_at", (did,)).fetchall()]


@pytest.mark.parametrize("timing", ["inflight", "returned"])
def test_close_save_after_ctrl_s_does_not_self_conflict(team, monkeypatch, timing):
    # inflight：Ctrl+S 的整篇保存还在检查时关页保存就到了；returned：Ctrl+S 已返回，但关页保存仍以打开时的修订为基准
    client, did, _ = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    saved_text = TEXT.replace("第一段。", "第一段。【存1】")
    out = {}
    if timing == "inflight":
        entered, release = threading.Event(), threading.Event()

        def slow_check(*args, **kwargs):
            entered.set()
            release.wait(5)
        monkeypatch.setattr("app.documents._check_word", slow_check)
        saver = threading.Thread(target=lambda: out.setdefault("ctrl", save(client, did, 1, saved_text, headers, key="ctrl-s-key-1")))
        saver.start()
        assert entered.wait(5)
        monkeypatch.setattr("app.documents._check_word", lambda *args, **kwargs: None)
        closer = threading.Thread(target=lambda: out.setdefault("close", close_patch(
            client, did, 1, p1, "第一段。【存1】【后1】", headers, ["ctrl-s-key-1"])))
        closer.start()
        threading.Timer(0.3, release.set).start()
        saver.join(15)
        closer.join(15)
    else:
        out["ctrl"] = save(client, did, 1, saved_text, headers, key="ctrl-s-key-1")
        out["close"] = close_patch(client, did, 1, p1, "第一段。【存1】【后1】", headers, ["ctrl-s-key-1"])
    assert out["ctrl"].status_code == 200, out["ctrl"].text
    assert out["close"].status_code == 200, out["close"].text
    doc = current(client, did)
    assert doc["revision"] == 3  # 打开时 1：Ctrl+S → 2，关页 → 3
    assert "第一段。【存1】【后1】" in doc["markdownContent"]
    assert conflicts(did) == []
    assert out["close"].json()["data"]["merged"] is False  # 只隔着本人的保存，不提示“别人改过”


def test_close_save_still_conflicts_when_someone_else_changed_paragraph(team):
    client, did, _ = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    assert save(client, did, 1, TEXT.replace("第一段。", "第一段。【存1】"), headers, key="ctrl-s-key-2").status_code == 200
    other = _as(client, "editor2")
    assert save(client, did, 2, TEXT.replace("第一段。", "B 改第一段。"), other, key="b-save-key-1").status_code == 200
    headers = _as(client, "editor")
    response = close_patch(client, did, 1, p1, "第一段。【存1】【后1】", headers, ["ctrl-s-key-2"])
    assert response.status_code == 409, response.text
    assert len(conflicts(did)) == 1


def test_unknown_save_keys_do_not_rebase(team):
    # 别人的保存键或不存在的键不算：没有对应本人保存，照常判冲突
    client, did, _ = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    other = _as(client, "editor2")
    assert save(client, did, 1, TEXT.replace("第一段。", "B 改第一段。"), other, key="b-save-key-2").status_code == 200
    headers = _as(client, "editor")
    response = close_patch(client, did, 1, p1, "我改第一段。", headers, ["b-save-key-2", "nonexistent-key"])
    assert response.status_code == 409, response.text


def test_reopen_auto_resolves_existing_self_conflict(team):
    # 旧版本留下的自冲突：关页保存不带键 → 409（与 Ctrl+S 冲突）；重开（GET 文档）时自动“保留我的”并记为已处理
    client, did, users = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    assert save(client, did, 1, TEXT.replace("第一段。", "第一段。【存1】"), headers, key="ctrl-s-key-3").status_code == 200
    assert close_patch(client, did, 1, p1, "第一段。【存1】【后1】", headers).status_code == 409
    [conflict] = conflicts(did)
    assert conflict["status"] == "open"
    doc = current(client, did)
    assert "第一段。【存1】【后1】" in doc["markdownContent"] and doc["revision"] == 3
    [conflict] = conflicts(did)
    assert conflict["status"] == "resolved"  # 保留记录，不删除
    summary = client.get(f"/api/v1/documents/{did}/conflicts/summary", headers=headers).json()["data"]
    assert summary["mineOpen"] == 0 and summary["mineHandled"] == 1


def test_reopen_keeps_real_conflict_with_other_user(team):
    client, did, users = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    other = _as(client, "editor2")
    assert save(client, did, 1, TEXT.replace("第一段。", "B 改第一段。"), other, key="b-save-key-3").status_code == 200
    headers = _as(client, "editor")
    assert close_patch(client, did, 1, p1, "我改第一段。", headers).status_code == 409
    doc = current(client, did)
    assert "B 改第一段。" in doc["markdownContent"] and doc["revision"] == 2
    [conflict] = conflicts(did)
    assert conflict["status"] == "open"


def test_reopen_keeps_conflict_when_other_user_changed_after_own_save(team):
    # 本人 Ctrl+S 后，别人又改了同一段，再出现的冲突夹着别人的修改 → 不自动处理
    client, did, users = team
    headers = _as(client, "editor")
    p1 = by_text(objects(client, did), "第一段。")
    assert save(client, did, 1, TEXT.replace("第一段。", "第一段。【存1】"), headers, key="ctrl-s-key-4").status_code == 200
    other = _as(client, "editor2")
    assert save(client, did, 2, TEXT.replace("第一段。", "B 改第一段。"), other, key="b-save-key-4").status_code == 200
    headers = _as(client, "editor")
    assert close_patch(client, did, 1, p1, "第一段。【存1】【后1】", headers).status_code == 409
    current(client, did)
    [conflict] = conflicts(did)
    assert conflict["status"] == "open"
