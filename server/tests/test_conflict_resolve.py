"""S5：冲突“处理并提交”合一接口（01 §8.2 用例 38–42）。"""

import threading

from app.db import connect
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

THREE = TEXT  # 第一段 / 第二段 / 第三段 / 引用段


def _conflict(client, did, base, mine_edits: dict[str, str], theirs_edits: dict[str, str]):
    """editor 先保存 theirs_edits，editor2 用旧基线保存 mine_edits → 返回 409 details。"""
    start = current(client, did)["markdownContent"]
    theirs = start
    for old, new in theirs_edits.items():
        theirs = theirs.replace(old, new)
    assert save(client, did, base, theirs, _as(client, "editor")).status_code == 200
    mine = start
    for old, new in mine_edits.items():
        mine = mine.replace(old, new)
    response = save(client, did, base, mine, _as(client, "editor2"))
    assert response.status_code == 409, response.text
    return response.json()["error"]["details"]


def _resolve(client, did, cid, base, items, key):
    return client.post(f"/api/v1/documents/{did}/conflicts/{cid}/resolve", headers=_as(client, "editor2"),
                       json={"clientSaveKey": key, "baseDocumentRevision": base, "items": items})


def test_atomic_reconflict_keeps_everything_open(team):
    # 用例 38
    client, did, _ = team
    details = _conflict(client, did, 1,
                        {"第一段。": "B1。", "第二段。": "B2。", "第三段。": "B3。"},
                        {"第一段。": "A1。", "第二段。": "A2。", "第三段。": "A3。"})
    assert details["conflicts"][0]["reason"] in {"MODIFIED", "STRUCTURE"}
    cid = details["conflictId"]
    with connect() as connection:
        payload_before = connection.execute("SELECT payload_json FROM document_conflicts WHERE id=%s", (cid,)).fetchone()["payload_json"]
    items = details["conflicts"]
    if len(items) != 3:
        # 三段连续修改被 diff 合成一个结构区间：同样验证原子性
        assert len(items) == 1
    # 对方又改了第二段
    text = current(client, did)["markdownContent"].replace("A2。", "A2 再改。")
    assert save(client, did, 2, text, _as(client, "editor")).status_code == 200
    request = [{"itemIndex": item["itemIndex"], "action": "keep_mine", "theirsRevision": item["theirs"]["revision"]}
               for item in items]
    again = _resolve(client, did, cid, 3, request, "resolve-key-1")
    assert again.status_code == 409, again.text
    error = again.json()["error"]
    assert error["code"] == "OBJECT_CONFLICT" and error["details"]["conflictId"] == cid
    assert any(item["attempt"] == 2 for item in error["details"]["conflicts"])
    assert "A2 再改。" in "".join(item["theirs"]["markdown"] for item in error["details"]["conflicts"])
    assert "B1。" not in current(client, did)["markdownContent"]
    with connect() as connection:
        statuses = {row["status"] for row in connection.execute(
            "SELECT status FROM document_conflict_items WHERE conflict_id=%s", (cid,)).fetchall()}
        assert statuses == {"open"}
        import json
        before = json.loads(payload_before)
        after = json.loads(connection.execute("SELECT payload_json FROM document_conflicts WHERE id=%s", (cid,)).fetchone()["payload_json"])
        assert after["mineMarkdown"] == before["mineMarkdown"] and after["conflicts"] == before["conflicts"]
        assert len(after["attempts"]) == 1


def test_idempotent_resolve_and_concurrent_keys(team):
    # 用例 39
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid, [item] = details["conflictId"], details["conflicts"]
    body = [{"itemIndex": 0, "action": "keep_mine", "theirsRevision": item["theirs"]["revision"]}]
    first = _resolve(client, did, cid, 2, body, "resolve-key-2")
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["items"][0]["status"] == "resolved" and data["conflictStatus"] == "resolved" and data["mineOpen"] == 0
    assert "B2。" in data["markdownContent"]
    again = _resolve(client, did, cid, 2, body, "resolve-key-2")
    assert again.status_code == 200 and again.json()["data"]["documentRevision"] == data["documentRevision"]
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM document_changes WHERE client_save_key='resolve-key-2'").fetchone()["n"] == 1
    reused = _resolve(client, did, cid, 2, [{"itemIndex": 0, "action": "use_theirs"}], "resolve-key-2")
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    other = _resolve(client, did, cid, 2, body, "resolve-key-3")
    assert other.status_code == 409 and other.json()["error"]["code"] == "CONFLICT_ALREADY_RESOLVED"
    assert other.json()["error"]["details"]["items"][0]["status"] == "resolved"


def test_two_keys_race_on_same_item(team, monkeypatch):
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid, [item] = details["conflictId"], details["conflicts"]
    barrier = threading.Barrier(2, timeout=10)
    monkeypatch.setattr("app.documents._check_word", lambda *args, **kwargs: barrier.wait())
    from fastapi.testclient import TestClient
    from app.main import create_app
    responses = []

    def run(key):
        with TestClient(create_app()) as own:
            responses.append(_resolve(own, did, cid, 2, [{"itemIndex": 0, "action": "keep_mine",
                                                          "theirsRevision": item["theirs"]["revision"]}], key))

    threads = [threading.Thread(target=run, args=(key,)) for key in ("race-key-01", "race-key-02")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert current(client, did)["markdownContent"].count("B2。") == 1
    with connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM document_changes WHERE kind='conflict_resolution'").fetchone()["n"] == 1


def test_six_actions(team):
    # 用例 40
    client, did, _ = team
    # use_theirs：正文不变，条目 discarded，mine 仍可读取
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    before = current(client, did)
    response = _resolve(client, did, details["conflictId"], before["revision"], [{"itemIndex": 0, "action": "use_theirs"}], "k-use-theirs")
    assert response.status_code == 200 and response.json()["data"]["items"][0]["status"] == "discarded"
    assert current(client, did)["markdownContent"] == before["markdownContent"]
    copy = client.get(f"/api/v1/documents/{did}/conflicts/{details['conflictId']}").json()["data"]
    assert copy["items"][0]["mine"]["markdown"] == "B2。" and "B2。" in copy["mineMarkdown"]
    resolved = client.get(f"/api/v1/documents/{did}/conflicts", params={"status": "resolved"}).json()["data"]["items"]
    assert [entry["action"] for entry in resolved] == ["use_theirs"]
    # manual
    revision = current(client, did)["revision"]
    details = _conflict(client, did, revision, {"A2。": "B 手改。"}, {"A2。": "A 再改。"})
    item = details["conflicts"][0]
    response = _resolve(client, did, details["conflictId"], revision + 1, [{"itemIndex": 0, "action": "manual",
                        "theirsRevision": item["theirs"]["revision"], "markdown": "两人合并后的第二段。"}], "k-manual")
    assert response.status_code == 200, response.text
    text = current(client, did)["markdownContent"]
    assert "两人合并后的第二段。" in text and "A 再改。" not in text
    empty = _resolve(client, did, details["conflictId"], revision + 1, [{"itemIndex": 0, "action": "manual", "markdown": " "}], "k-empty1")
    assert empty.status_code in {409, 422}
    # keep_both：插在冲突对象之后
    revision = current(client, did)["revision"]
    details = _conflict(client, did, revision, {"第一段。": "B 的第一段。"}, {"第一段。": "A 的第一段。"})
    response = _resolve(client, did, details["conflictId"], revision + 1, [{"itemIndex": 0, "action": "keep_both"}], "k-keep-both")
    assert response.status_code == 200, response.text
    text = current(client, did)["markdownContent"]
    assert text.index("A 的第一段。") < text.index("B 的第一段。") < text.index("两人合并后的第二段。")
    # restore_as_new（section_end）：我改的段落被对方删除
    revision = current(client, did)["revision"]
    details = _conflict(client, did, revision, {"第三段。": "B 改第三段。"}, {"第三段。\n\n": ""})
    [item] = details["conflicts"]
    assert item["reason"] == "DELETED" and item["theirs"]["deleted"] is True
    assert item["theirs"]["deletedBy"]["name"] == "成员editor"
    response = _resolve(client, did, details["conflictId"], revision + 1,
                        [{"itemIndex": 0, "action": "restore_as_new", "placement": {"mode": "section_end"}}], "k-restore-1")
    assert response.status_code == 200, response.text
    text = current(client, did)["markdownContent"]
    assert text.index("B 改第三段。") < text.index("# 2 引用")
    # restore_as_new（after）+ discard
    revision = current(client, did)["revision"]
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    anchor = next(obj["id"] for obj in objects if obj["markdown"] == "引用段。")
    details = _conflict(client, did, revision, {"B 改第三段。": "B 再改第三段。"}, {"B 改第三段。\n\n": ""})
    response = _resolve(client, did, details["conflictId"], revision + 1,
                        [{"itemIndex": 0, "action": "restore_as_new", "placement": {"mode": "after", "afterObjectId": anchor}}],
                        "k-restore-2")
    assert response.status_code == 200, response.text
    assert current(client, did)["markdownContent"].rstrip().endswith("B 再改第三段。")
    revision = current(client, did)["revision"]
    details = _conflict(client, did, revision, {"B 再改第三段。": "又一版。"}, {"\n\nB 再改第三段。": ""})
    response = _resolve(client, did, details["conflictId"], revision + 1, [{"itemIndex": 0, "action": "discard"}], "k-discard")
    assert response.status_code == 200 and "又一版。" not in current(client, did)["markdownContent"]


def test_table_keep_both_inserts_whole_table(team):
    client, did, _ = team
    table = "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |"
    text = current(client, did)["markdownContent"].replace("引用段。", f"引用段。\n\n{table}")
    assert save(client, did, 1, text, _as(client, "editor")).status_code == 200
    details = _conflict(client, did, 2, {"| 1 | 2 |": "| 1 | B |"}, {"| 1 | 2 |": "| 1 | A |"})
    assert details["conflicts"][0]["location"]["kind"] == "table"
    response = _resolve(client, did, details["conflictId"], 3, [{"itemIndex": 0, "action": "keep_both"}], "k-table-1")
    assert response.status_code == 200, response.text
    final = current(client, did)["markdownContent"]
    assert "| 1 | A |" in final and "| 1 | B |" in final and final.count("| 甲 | 乙 |") == 2


def test_only_author_resolves_and_format_failure_keeps_open(team, monkeypatch):
    # 用例 41、42
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    for role in ("owner", "editor"):
        headers = _as(client, role)
        response = client.post(f"/api/v1/documents/{did}/conflicts/{cid}/resolve", headers=headers,
                               json={"clientSaveKey": f"{role}-key-1", "baseDocumentRevision": 2,
                                     "items": [{"itemIndex": 0, "action": "use_theirs"}]})
        assert response.status_code == 403
    from app.errors import AppError

    def failing(*args, **kwargs):
        raise AppError(422, "AI_FORMAT_INVALID", "第 1 个表格：无法写回。", details={"location": {"headingPath": ["1 范围"]}})

    monkeypatch.setattr("app.documents._check_word", failing)
    response = _resolve(client, did, cid, 2, [{"itemIndex": 0, "action": "keep_mine",
                                              "theirsRevision": details["conflicts"][0]["theirs"]["revision"]}], "k-format-1")
    assert response.status_code == 422 and response.json()["error"]["details"]["location"]["headingPath"] == ["1 范围"]
    copy = client.get(f"/api/v1/documents/{did}/conflicts/{cid}").json()["data"]
    assert copy["status"] == "open" and copy["items"][0]["mine"]["markdown"] == "B2。"
    assert copy["mineMarkdown"] == TEXT.replace("第二段。", "B2。")
