"""阶段2 审查（10-阶段2审查.md）修复的回归测试。"""
import json

from app.db import connect
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401


def _resolve(client, did, cid, headers, base, items, key):
    return client.post(f"/api/v1/documents/{did}/conflicts/{cid}/resolve", headers=headers,
                       json={"clientSaveKey": key, "baseDocumentRevision": base, "items": items})


def test_s1_theirs_deleted_keep_mine_restores_in_place(team):
    client, did, _users = team
    assert save(client, did, 1, TEXT.replace("第二段。\n\n", ""), _as(client, "editor")).status_code == 200
    b = _as(client, "editor2")
    r = save(client, did, 1, TEXT.replace("第二段。", "B 改的第二段。"), b)
    assert r.status_code == 409
    details = r.json()["error"]["details"]
    assert details["conflicts"][0]["reason"] == "DELETED"
    resp = _resolve(client, did, details["conflictId"], b, 2, [{"itemIndex": 0, "action": "keep_mine"}], "s1-keep-mine-1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["markdownContent"].startswith("# 1 范围\n\n第一段。\n\nB 改的第二段。\n\n第三段。")
    # 界面验收 M2：全部处理后 summary 仍给出已处理条数，前端据此保留“冲突”入口
    assert client.get(f"/api/v1/documents/{did}/conflicts/summary").json()["data"] == {"mineOpen": 0, "mineHandled": 1, "allOpen": None}


def test_s1_reconflict_keeps_object_ids_for_placement(team):
    client, did, _users = team
    assert save(client, did, 1, TEXT.replace("第二段。", "A 的第二段。"), _as(client, "editor")).status_code == 200
    r = save(client, did, 1, TEXT.replace("第二段。", "B 的第二段。"), _as(client, "editor2"))
    cid = r.json()["error"]["details"]["conflictId"]
    # 对方随后把这段删掉：keep_mine 再次冲突，但条目仍记住原对象（用于定位）
    base = current(client, did)["revision"]
    assert save(client, did, base, TEXT.replace("A 的第二段。\n\n", "").replace("第二段。\n\n", ""), _as(client, "editor")).status_code == 200
    b = _as(client, "editor2")
    again = _resolve(client, did, cid, b, base + 1, [{"itemIndex": 0, "action": "keep_mine"}], "s1-reconflict-1")
    assert again.status_code == 409
    with connect() as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM document_conflicts WHERE id=%s", (cid,)).fetchone()["payload_json"])
    snap = payload["attempts"][-1]["items"]["0"]
    assert snap["objectIds"] and snap["goneObjectIds"] == snap["objectIds"]
    keep_both = _resolve(client, did, cid, b, base + 1, [{"itemIndex": 0, "action": "keep_both"}], "s1-keep-both-1")
    assert keep_both.status_code == 200, keep_both.text
    assert keep_both.json()["data"]["markdownContent"].startswith("# 1 范围\n\n第一段。\n\nB 的第二段。")
