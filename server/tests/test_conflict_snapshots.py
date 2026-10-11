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
    # 浏览器的 tabId 是 UUID 形式：409 的条目（含 mineLatest.tabId）要能序列化（e2e 发现曾 500）
    tab = "7cb2bfc2-4c92-4fe9-96b3-ea7652948d2a"
    assert _mine(client, did, cid, 0, "B2。续写。", "snap-recon-01", tab=tab).status_code == 200
    revision = current(client, did)["revision"]
    again = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_mine",
                                                  "theirsRevision": item["theirs"]["revision"]}], "res-recon-01")
    assert again.status_code == 409, again.text
    assert again.json()["error"]["details"]["conflicts"][0]["mineLatest"]["tabId"] == tab
    [fresh] = again.json()["error"]["details"]["conflicts"]
    assert fresh["attempt"] == 2
    assert fresh["theirs"]["updatedBy"]["name"] == "成员editor"  # 不把自己当对方
    assert fresh["base"]["markdown"] == "第二段。"


# ---- 代码审查员对方案 C 的重点审查项（efb41bc 增量审查追加）----

def test_mine_access_author_only_admin_readonly_outsider_404(team):
    """审查点 1：只有冲突作者可追加；负责人、管理员只读（403）；非成员 404；已丢弃（使用对方）的条目 409。"""
    from app.identity import create_user
    from tests.test_document_access import PASSWORD, _login
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    with connect() as connection:
        create_user(account="m-sysadmin", name="系统管理员", password=PASSWORD, roles=["system_admin"], connection=connection)
        connection.commit()
    assert _mine(client, did, cid, 0, "x", "acc-owner-01", role="owner").status_code == 403
    client.cookies.clear()
    admin = _login(client, "m-sysadmin")
    r = client.post(f"/api/v1/documents/{did}/conflicts/{cid}/mine", headers=admin,
                    json={"itemIndex": 0, "markdown": "x", "clientSaveKey": "acc-admin-01"})
    assert r.status_code == 403, r.text
    assert _mine(client, did, cid, 0, "x", "acc-out-0001", role="outsider").status_code == 404
    _as(client, "editor2")
    revision = current(client, did)["revision"]
    assert _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "use_theirs"}], "res-acc-0001").status_code == 200
    r = _mine(client, did, cid, 0, "处理后", "acc-late-001")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT_ALREADY_RESOLVED"


def _mine_seq(client, did, cid, markdown, key, seq, tab="tab-seq"):
    return client.post(f"/api/v1/documents/{did}/conflicts/{cid}/mine", headers=_as(client, "editor2"),
                       json={"itemIndex": 0, "markdown": markdown, "clientSaveKey": key, "tabId": tab, "seq": seq})


def test_out_of_order_snapshot_does_not_override_latest(team):
    """审查点 2：同一标签页乱序到达的旧快照（序号更小）不写入、不覆盖 mineLatest；重放同键返回原结果；别的标签各自追加。"""
    client, did, _ = team
    cid = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})["conflictId"]
    assert _mine_seq(client, did, cid, "B2。新", "seq-key-0003", 3).status_code == 200
    old = _mine_seq(client, did, cid, "B2。旧", "seq-key-0002", 2)
    assert old.status_code == 200 and old.json()["data"]["stale"] is True
    assert old.json()["data"]["mineLatest"]["markdown"] == "B2。新"
    same_seq_unchanged = _mine_seq(client, did, cid, "B2。新", "seq-key-0004", 4)
    assert same_seq_unchanged.status_code == 200
    assert _mine_seq(client, did, cid, "B2。更旧", "seq-key-0001", 3).json()["data"]["stale"] is True
    assert _mine_seq(client, did, cid, "B2。新", "seq-key-0003", 3).json()["data"]["replayed"] is True
    other_tab = _mine_seq(client, did, cid, "B2。另一个标签", "seq-key-b001", 1, tab="tab-other")
    assert other_tab.status_code == 200 and not other_tab.json()["data"]["stale"]
    [item] = _open_items(client, did)
    assert item["mineLatest"]["markdown"] == "B2。另一个标签"
    with connect() as connection:
        rows = connection.execute("SELECT markdown FROM document_conflict_mine_snapshots WHERE conflict_id=%s ORDER BY rowid",
                                  (cid,)).fetchall()
    assert [row[0] for row in rows] == ["B2。新", "B2。新", "B2。另一个标签"]  # 只追加，旧快照没写进去


def test_snapshot_count_and_size_limits(team, monkeypatch):
    client, did, _ = team
    cid = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})["conflictId"]
    monkeypatch.setattr("app.document_commit.MAX_SNAPSHOTS_PER_ITEM", 3)
    for n in range(3):
        assert _mine(client, did, cid, 0, f"B2。第{n}版", f"cap-key-000{n}").status_code == 200
    r = _mine(client, did, cid, 0, "B2。第4版", "cap-key-0004")
    assert r.status_code == 422 and r.json()["error"]["code"] == "SNAPSHOT_LIMIT"
    assert _mine(client, did, cid, 0, "B2。第0版", "cap-key-0000").status_code == 200  # 重放不受上限影响
    assert _mine(client, did, cid, 0, "长" * 2_000_001, "cap-key-big1").status_code == 422


def test_resolve_checks_client_seen_snapshot_and_records_it_for_all_actions(team):
    """审查点 3：resolve 带客户端看到的快照 id，不是最新一份 → 409 MINE_SNAPSHOT_CHANGED；一致时用它并记录。"""
    client, did, _ = team
    cid = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})["conflictId"]
    first = _mine(client, did, cid, 0, "B2。第一版", "chk-key-0001").json()["data"]["mineLatest"]["snapshotId"]
    second = _mine(client, did, cid, 0, "B2。第二版", "chk-key-0002").json()["data"]["mineLatest"]["snapshotId"]
    revision = current(client, did)["revision"]
    stale = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_mine", "mineSnapshotId": first}], "res-chk-0001")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "MINE_SNAPSHOT_CHANGED", stale.text
    assert stale.json()["error"]["details"]["mineLatest"]["markdown"] == "B2。第二版"
    assert "B2。" not in current(client, did)["markdownContent"].replace("B2。第", "")  # 正文未动
    ok = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "use_theirs", "mineSnapshotId": second}], "res-chk-0002")
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["items"][0]["mineSnapshotId"] == second


def test_resolve_and_snapshot_race_uses_latest(team, monkeypatch):
    """审查点 3：resolve 读到快照后、提交前又追加了新快照——不能用旧快照完成处理（按新快照重来）。"""
    import app.document_commit as dc
    client, did, _ = team
    cid = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})["conflictId"]
    _mine(client, did, cid, 0, "B2。旧快照", "race-key-001")
    original = dc._apply_actions
    fired = {"n": 0}

    def racing(connection, did_, payload, items, latest=None):
        out = original(connection, did_, payload, items, latest)
        if not fired["n"]:
            fired["n"] = 1
            with connect() as other:
                other.execute("BEGIN IMMEDIATE")
                dc.append_snapshot(other, conflict_id=cid, item_index=0, actor=payload.get("actorId") or _actor(cid),
                                   markdown="B2。并发新快照", key="race-key-002")
                other.commit()
        return out

    monkeypatch.setattr(dc, "_apply_actions", racing)
    revision = current(client, did)["revision"]
    r = _resolve(client, did, cid, revision, [{"itemIndex": 0, "action": "keep_mine"}], "res-race-001")
    assert r.status_code == 200, r.text
    assert "B2。并发新快照" in current(client, did)["markdownContent"]
    assert "B2。旧快照" not in current(client, did)["markdownContent"]


def _actor(cid):
    with connect() as connection:
        return connection.execute("SELECT actor_id FROM document_conflicts WHERE id=%s", (cid,)).fetchone()[0]


def test_migration_115_sql_is_idempotent(tmp_path, monkeypatch):
    """审查点 4：迁移 115 重复执行不报错（回滚脚本的幂等见 test_rollback_migration_115）。"""
    import sqlite3
    from pathlib import Path
    from app import config
    from app.db import migrate
    path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    config.get_settings.cache_clear()
    try:
        migrate()
        sql = (Path(__file__).resolve().parents[1] / "migrations" / "115_document_conflict_mine_snapshots.sql").read_text(encoding="utf-8")
        with sqlite3.connect(path) as connection:
            connection.executescript(sql)
            connection.executescript(sql)
            columns = [row[1] for row in connection.execute("PRAGMA table_info(document_conflict_mine_snapshots)")]
        assert "seq" in columns
        assert migrate() == []
    finally:
        config.get_settings.cache_clear()


def test_downgraded_author_cannot_append_snapshot(team):
    """已定（审查建议 2 / 测试 K4-L）：作者被降为查看者后不能再存快照，返回 403。"""
    client, did, users = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    assert _mine(client, did, cid, 0, "B2。降级前。", "down-key-0001").status_code == 200
    with connect() as connection:
        connection.execute("UPDATE project_members SET role='viewer' WHERE user_id=%s", (users["editor2"],))
        connection.commit()
    denied = _mine(client, did, cid, 0, "B2。降级后。", "down-key-0002")
    assert denied.status_code == 403, denied.text
    [item] = _open_items(client, did)
    assert item["mineLatest"]["markdown"] == "B2。降级前。"


def test_snapshot_total_bytes_cap_per_conflict(team, monkeypatch):
    """已定（审查建议 1）：每条冲突所有快照合计不超过上限（20MB），超过返回 422 SNAPSHOT_LIMIT。"""
    client, did, _ = team
    monkeypatch.setattr("app.document_commit.MAX_SNAPSHOT_BYTES_PER_CONFLICT", 30)
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    assert _mine(client, did, cid, 0, "一二三四五", "cap-key-0001").status_code == 200  # 15 字节
    over = _mine(client, did, cid, 0, "一二三四五六", "cap-key-0002")  # 再 18 字节 → 33 > 30
    assert over.status_code == 422 and over.json()["error"]["code"] == "SNAPSHOT_LIMIT"
    assert "20MB" in over.json()["error"]["message"]
    # 同键重放不受上限影响
    assert _mine(client, did, cid, 0, "一二三四五", "cap-key-0001").status_code == 200


def _snapshot_rows(cid):
    with connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(CAST(markdown AS BLOB))), 0) AS b "
                                 "FROM document_conflict_mine_snapshots WHERE conflict_id=%s", (cid,)).fetchone()
        return int(row["n"]), int(row["b"])


def test_resubmit_frozen_snapshots_count_toward_size_and_count_caps(team, monkeypatch):
    """审查建议 4：从旧修订重交冻结段（resubmit → _append_frozen）也计入 20MB / 500 份上限，不能绕过；
    超限时冻结段不再追加快照，正文其余修改照常保存，响应里 snapshotLimited 标出是哪一处。"""
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    start = current(client, did)
    first = start["markdownContent"].replace("A2。", "B2。续写一。")
    ok = save(client, did, 1, first, _as(client, "editor2"), key="cap-resubmit-01")
    assert ok.status_code == 200, ok.text
    assert "snapshotLimited" not in ok.json()["data"]
    count, used = _snapshot_rows(cid)
    monkeypatch.setattr("app.document_commit.MAX_SNAPSHOT_BYTES_PER_CONFLICT", used + 10)
    big = start["markdownContent"].replace("A2。", "B2。续写一。再写很多很多字。").replace("第一段。", "B 改第一段。")
    over = save(client, did, 1, big, _as(client, "editor2"), key="cap-resubmit-02")
    assert over.status_code == 200, over.text
    assert over.json()["data"]["snapshotLimited"] == [{"conflictId": cid, "itemIndex": 0}]
    assert _snapshot_rows(cid) == (count, used)  # 没有绕过总大小上限
    assert "B 改第一段。" in current(client, did)["markdownContent"]  # 其余修改照常保存
    # 与最新一份相同的内容不算新增，不受上限影响
    same = start["markdownContent"].replace("A2。", "B2。续写一。").replace("第三段。", "B 改第三段。")
    again = save(client, did, 1, same, _as(client, "editor2"), key="cap-resubmit-03")
    assert again.status_code == 200 and "snapshotLimited" not in again.json()["data"], again.text
    # 份数上限同样生效
    monkeypatch.setattr("app.document_commit.MAX_SNAPSHOT_BYTES_PER_CONFLICT", 20 * 1024 * 1024)
    monkeypatch.setattr("app.document_commit.MAX_SNAPSHOTS_PER_ITEM", count)
    more = save(client, did, 1, start["markdownContent"].replace("A2。", "B2。续写二。"), _as(client, "editor2"), key="cap-resubmit-04")
    assert more.status_code == 200 and more.json()["data"]["snapshotLimited"] == [{"conflictId": cid, "itemIndex": 0}], more.text
    assert _snapshot_rows(cid)[0] == count


def test_mine_others_lists_last_snapshot_of_other_tabs(team):
    """审查建议 1（A3c）：列表带 mineOthers——其他标签页各自最后一份（与最新一份不同的），前端据此在处理前确认。"""
    client, did, _ = team
    details = _conflict(client, did, 1, {"第二段。": "B2。"}, {"第二段。": "A2。"})
    cid = details["conflictId"]
    assert _mine(client, did, cid, 0, "B2。甲一。", "others-key-0001", tab="tab-a").status_code == 200
    assert _open_items(client, did)[0]["mineOthers"] == []
    assert _mine(client, did, cid, 0, "B2。乙的字。", "others-key-0002", tab="tab-b").status_code == 200
    assert _mine(client, did, cid, 0, "B2。甲一。甲后写。", "others-key-0003", tab="tab-a").status_code == 200
    [item] = _open_items(client, did)
    assert item["mineLatest"]["markdown"] == "B2。甲一。甲后写。"
    assert [(o["tabId"], o["markdown"]) for o in item["mineOthers"]] == [("tab-b", "B2。乙的字。")]
    # 乙标签合并后成为最新一份：列出的是甲标签的最后一份，不再列乙
    assert _mine(client, did, cid, 0, "B2。甲一。甲后写。乙的字。", "others-key-0004", tab="tab-b").status_code == 200
    [item] = _open_items(client, did)
    assert [(o["tabId"], o["markdown"]) for o in item["mineOthers"]] == [("tab-a", "B2。甲一。甲后写。")]
