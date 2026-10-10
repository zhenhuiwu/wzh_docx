"""方案 C：冲突段冻结 + “我的版本”只追加快照（14-最终界面验收 §四 后端 1–5）。"""

import json

from app.db import connect
from tests.test_conflict_resolve import _conflict, _resolve
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401


def _mine(client, did, cid, index, markdown, key, role="editor2", tab="tab-1"):
    return client.post(f"/api/v1/documents/{did}/conflicts/{cid}/mine", headers=_as(client, role),
                       json={"itemIndex": index, "markdown": markdown, "clientSaveKey": key, "tabId": tab})


def _open_items(client, did, role="editor2"):
    return client.get(f"/api/v1/documents/{did}/conflicts", headers=_as(client, role)).json()["data"]["items"]


def test_snapshot_append_idempotent_author_only_and_listed(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    assert details["conflicts"][0]["mineLatest"] == {"markdown": "B2。", "savedAt": None, "tabId": None, "snapshotId": None}
    first = _mine(client, did, cid, 0, "B2。续写一句。", "snap-key-0001")
    assert first.status_code == 200, first.text
    latest = first.json()["data"]["mineLatest"]
    assert latest["markdown"] == "B2。续写一句。" and latest["savedAt"] and latest["tabId"] == "tab-1"
    again = _mine(client, did, cid, 0, "B2。续写一句。", "snap-key-0001")
    assert again.status_code == 200 and again.json()["data"]["replayed"] is True
    assert again.json()["data"]["mineLatest"]["snapshotId"] == latest["snapshotId"]
    reused = _mine(client, did, cid, 0, "别的内容", "snap-key-0001")
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    for role in ("editor", "owner"):
        assert _mine(client, did, cid, 0, "冒充", f"snap-{role}-01", role=role).status_code == 403
    assert _mine(client, did, cid, 5, "x", "snap-key-0009").status_code == 422
    second = _mine(client, did, cid, 0, "B2。续写一句。再写一句。", "snap-key-0002")
    assert second.status_code == 200
    [item] = _open_items(client, did)
    assert item["mine"]["markdown"] == "B2。" and item["mineLatest"]["markdown"] == "B2。续写一句。再写一句。"
    detail = client.get(f"/api/v1/documents/{did}/conflicts/{cid}", headers=_as(client, "editor2")).json()["data"]
    assert detail["items"][0]["mineLatest"]["markdown"] == "B2。续写一句。再写一句。"
    with connect() as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM document_conflicts WHERE id=%s", (cid,)).fetchone()[0])
        assert payload["conflicts"][0]["mine"]["markdown"] == "B2。"  # 原始内容永不修改
        assert connection.execute("SELECT COUNT(*) FROM document_conflict_mine_snapshots WHERE conflict_id=%s",
                                  (cid,)).fetchone()[0] == 2
    # 处理后不能再写；同键重放仍返回原结果
    revision = current(client, did)["revision"]
    assert _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "use_theirs"}], "res-snap-01").status_code == 200
    closed = _mine(client, did, cid, 0, "处理后再写", "snap-key-0003")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "CONFLICT_ALREADY_RESOLVED"
    assert _mine(client, did, cid, 0, "B2。续写一句。再写一句。", "snap-key-0002").status_code == 200


def test_keep_mine_uses_latest_multi_block_snapshot(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2 前半句"}, {"第二段。": "A2。"})
    cid, [item] = details["conflictId"], details["conflicts"]
    # 冲突后在冻结段里回车拆成两段，并继续写
    assert _mine(client, did, cid, 0, "B2 前半句\n\n后半句也在。", "snap-multi-01").status_code == 200
    revision = current(client, did)["revision"]
    response = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_mine",
                                                     "theirsRevision": item["theirs"]["revision"]}], "res-multi-01")
    assert response.status_code == 200, response.text
    result = response.json()["data"]["items"][0]
    assert result["mineSnapshotId"] and result["mineSavedAt"]
    text = current(client, did)["markdownContent"]
    assert "B2 前半句\n\n后半句也在。" in text and "A2。" not in text and text.count("后半句也在。") == 1
    with connect() as connection:
        stored = json.loads(connection.execute(
            "SELECT result_json FROM document_changes WHERE client_save_key='res-multi-01'").fetchone()[0])
        assert stored["items"][0]["mineSnapshotId"] == result["mineSnapshotId"]


def test_keep_both_and_restore_as_new_use_snapshot(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第一段。": "B 的第一段。"}, {"第一段。": "A 的第一段。"})
    cid = details["conflictId"]
    assert _mine(client, did, cid, 0, "B 的第一段。续写。", "snap-both-01").status_code == 200
    revision = current(client, did)["revision"]
    assert _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_both"}], "res-both-01").status_code == 200
    text = current(client, did)["markdownContent"]
    assert text.index("A 的第一段。") < text.index("B 的第一段。续写。")  # 对方在前、我的在后
    revision = current(client, did)["revision"]
    details = _conflict(client, did, revision, {"第三段。": "B 改第三段。"}, {"第三段。\n\n": ""})
    cid = details["conflictId"]
    assert details["conflicts"][0]["reason"] == "DELETED"
    assert _mine(client, did, cid, 0, "B 改第三段。还续写了。", "snap-rest-01").status_code == 200
    response = _resolve(client, did, cid, revision + 1, [{"itemIndex": 0, "action": "restore_as_new"}], "res-rest-01")
    assert response.status_code == 200, response.text
    assert "B 改第三段。还续写了。" in current(client, did)["markdownContent"]


def test_empty_snapshot_only_allows_theirs_or_manual(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid, [item] = details["conflictId"], details["conflicts"]
    assert _mine(client, did, cid, 0, "", "snap-empty-01").status_code == 200
    revision = current(client, did)["revision"]
    for action in ("keep_mine", "keep_both"):
        response = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": action,
                                                         "theirsRevision": item["theirs"]["revision"]}], f"res-empty-{action}")
        assert response.status_code == 422 and response.json()["error"]["code"] == "MINE_EMPTY"
    assert _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "use_theirs"}], "res-empty-ok").status_code == 200


def test_same_user_same_open_block_is_frozen_not_new_conflict(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    start = current(client, did)
    # 同一人用旧基线再次提交同一段（含续写）+ 另一段的修改
    mine = start["markdownContent"].replace("A2。", "B2。冲突后续写。").replace("第一段。", "B 改第一段。")
    response = save(client, did, 1, mine, _as(client, "editor2"), key="frozen-save-01")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["frozenByConflict"] == [cid]
    text = current(client, did)["markdownContent"]
    assert "A2。" in text and "冲突后续写" not in text and "B 改第一段。" in text  # 冻结段用正文版本，其他段照常保存
    [item] = _open_items(client, did)
    assert item["conflictId"] == cid and item["attempt"] == 1
    assert item["mineLatest"]["markdown"] == "B2。冲突后续写。" and item["mine"]["markdown"] == "B2。"
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_conflicts WHERE document_id=%s", (did,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_conflict_items WHERE action='superseded'").fetchone()[0] == 0
    # 同键重放：同一结果，不重复写快照
    again = save(client, did, 1, mine, _as(client, "editor2"), key="frozen-save-01")
    assert again.status_code == 200 and again.json()["data"]["frozenByConflict"] == [cid]
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_conflict_mine_snapshots WHERE conflict_id=%s",
                                  (cid,)).fetchone()[0] == 1


def test_reconflict_other_party_is_last_non_self_editor_and_base_unchanged(team):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid, [item] = details["conflictId"], details["conflicts"]
    assert item["theirs"]["updatedBy"]["name"] == "成员editor"
    # 本人在最新版本上直接改了这一段（如另一个标签页），对象修改人变成本人
    start = current(client, did)
    assert save(client, did, start["revision"], start["markdownContent"].replace("A2。", "A2。本人补一句。"),
                _as(client, "editor2")).status_code == 200
    revision = current(client, did)["revision"]
    again = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_mine",
                                                  "theirsRevision": item["theirs"]["revision"]}], "res-recon-01")
    assert again.status_code == 409
    [fresh] = again.json()["error"]["details"]["conflicts"]
    assert fresh["attempt"] == 2
    assert fresh["theirs"]["updatedBy"]["name"] == "成员editor"  # 不把自己当对方
    assert fresh["base"]["markdown"] == "第二段。"
