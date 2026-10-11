"""合并提交、冲突副本与“处理并提交”（01-阶段2设计 §4.3、§4.4、§5.3、§5.4）。

提交流程（5.4）：幂等重放 → 最多 3 轮“锁外合并 + Word 预检，BEGIN IMMEDIATE 内 CAS 写入” → 第 4 轮在进程内文档锁里重做。
冲突副本在独立短事务里先落库，再返回 409；payload_json 中“我的内容”永不修改、永不删除（决策5）。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
import sqlite3
import threading
import weakref
from contextlib import nullcontext
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect, write_transaction
from app.document_access import document_principal, is_admin, load_document
from app.document_merge import Entry, MergeResult, assemble_entries, merge3
from app.document_objects import (_changed_by, ensure_committed, ensure_document_objects, live_objects, locations_of)
from app.errors import AppError
from app.markdown_blocks import Block, heading_text, split
from app.models import format_timestamp

router = APIRouter()

# 审查 S8：弱引用字典，调用方持有锁对象期间条目存在，之后自动回收
_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()
OPTIMISTIC_ATTEMPTS = 3


def document_lock(document_id: UUID) -> threading.Lock:
    """进程内按文档提交锁（单进程前提，5.4 第 4 轮）。"""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(document_id), threading.Lock())


class _Retry(Exception):
    pass


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _person(user_id, name) -> dict | None:
    return None if user_id is None else {"id": str(user_id), "name": name}


def _joined(blocks: list[Block]) -> str:
    return "\n\n".join(block.body for block in blocks)


# ---------------------------------------------------------------- 幂等

def _version_markdown(connection, document_id: UUID, revision: int) -> str | None:
    row = connection.execute("SELECT markdown_content FROM document_versions WHERE document_id=%s AND revision=%s",
                             (document_id, revision)).fetchone()
    return None if row is None else row["markdown_content"]


def replay(connection, document_id: UUID, actor: UUID, key: str | None, rhash: str) -> dict | None:
    """同键同请求体 → 第一次的结果；同键不同请求体 → 422；同请求体仍有未处理冲突 → 同一个 409。"""
    if key:
        row = connection.execute(
            "SELECT request_hash, result_json, doc_revision FROM document_changes "
            "WHERE document_id=%s AND actor_id=%s AND client_save_key=%s", (document_id, actor, key),
        ).fetchone()
        if row is not None:
            if row["request_hash"] != rhash:
                raise AppError(422, "IDEMPOTENCY_KEY_REUSED", "保存键已用于另一份内容，请重新保存。")
            result = json.loads(row["result_json"] or "{}")
            result["markdownContent"] = _version_markdown(connection, document_id, int(row["doc_revision"]))
            result["replayed"] = True
            return result
    existing = connection.execute(
        "SELECT id FROM document_conflicts WHERE document_id=%s AND actor_id=%s AND request_hash=%s AND status='open'",
        (document_id, actor, rhash),
    ).fetchone()
    if existing is not None:
        raise conflict_error(connection, existing["id"])
    return None


# ---------------------------------------------------------------- 冲突副本

def _live_map(connection, document_id: UUID, blocks: list[Block]) -> list | None:
    live = live_objects(connection, document_id)
    if len(live) == len(blocks) and all(row["body"] == block.body for row, block in zip(live, blocks)):
        return live
    return None


def _names(connection, ids) -> dict[str, str]:
    ids = [str(value) for value in ids if value is not None]
    if not ids:
        return {}
    rows = connection.execute("SELECT id, name FROM users WHERE id IN (SELECT value FROM json_each(%s))",
                              (json.dumps(ids),)).fetchall()
    return {str(row["id"]): row["name"] for row in rows}


def _deleted_object(connection, document_id: UUID, body: str, since: int):
    return connection.execute(
        """SELECT o.id, o.revision, o.position, o.deleted_at, c.actor_id, u.name FROM document_objects o
           LEFT JOIN document_object_changes oc ON oc.object_id=o.id AND oc.op='delete'
           LEFT JOIN document_changes c ON c.id=oc.change_id LEFT JOIN users u ON u.id=c.actor_id
           WHERE o.document_id=%s AND o.deleted_at IS NOT NULL AND o.deleted_doc_revision > %s AND o.body=%s
           ORDER BY o.deleted_doc_revision DESC LIMIT 1""",
        (document_id, since, body),
    ).fetchone()


def _last_other_editor(connection, object_ids: list[str], actor) -> dict | None:
    """方案 C 后端第 5 条：区间里最近一次由“别人”（非本人）做的修改。"""
    if not object_ids or actor is None:
        return None
    return connection.execute(
        """SELECT c.actor_id, u.name, c.created_at FROM document_object_changes oc
           JOIN document_changes c ON c.id=oc.change_id LEFT JOIN users u ON u.id=c.actor_id
           WHERE oc.object_id IN (SELECT value FROM json_each(%s)) AND c.actor_id IS NOT NULL AND c.actor_id<>%s
             AND c.kind NOT IN ('backfill','realign')
           ORDER BY c.doc_revision DESC LIMIT 1""",
        (json.dumps([str(value) for value in object_ids]), str(actor))).fetchone()


def _theirs_info(connection, document_id: UUID, rows: list, names: dict, base_bodies: set[str] | None = None,
                 actor=None) -> dict:
    # 增量审查 M1：冲突区间可能连带自己刚改过、与基准相同的相邻段；“对方”取区间里相对基准真正变了的那段的修改人
    changed = [row for row in rows if base_bodies is None or row["body"] not in base_bodies]
    first = max(changed, key=lambda row: int(row["updated_doc_revision"] or 0)) if changed else rows[0]
    who = _person(first["updated_by"], names.get(str(first["updated_by"])))
    when = format_timestamp(first["updated_at"])
    by_self = actor is not None and first["updated_by"] is not None and str(first["updated_by"]) == str(actor)
    if by_self:
        # 方案 C 后端第 5 条：最近一次是本人改的（如冲突后又提交了同段），“对方”取最后一个非本人修改者，不把自己当对方
        other = _last_other_editor(connection, [str(row["id"]) for row in rows], actor)
        if other is not None:
            who = _person(other["actor_id"], other["name"])
            when = format_timestamp(other["created_at"])
    return {"markdown": "\n\n".join(row["body"] for row in rows), "revision": int(first["revision"]), "deleted": False,
            "updatedBy": who, "updatedAt": when, **({"latestBySelf": True} if by_self else {})}


def build_items(connection, document_id: UUID, base_md: str, theirs_md: str, result: MergeResult,
                base_revision: int, actor=None) -> list[dict]:
    _, base_blocks = split(base_md)
    _, theirs_blocks = split(theirs_md)
    live = _live_map(connection, document_id, theirs_blocks)
    names = _names(connection, [row["updated_by"] for row in live or []])
    loc_t = locations_of([(b.kind, b.body, b.heading_level) for b in theirs_blocks])
    loc_b = locations_of([(b.kind, b.body, b.heading_level) for b in base_blocks])
    items: list[dict] = []
    for index, conflict in enumerate(result.conflicts):
        t1, t2 = conflict.theirs_range
        rows = live[t1:t2] if live is not None else []
        item: dict = {"itemIndex": index, "attempt": 1, "reason": conflict.reason, "opIndexes": [],
                      "objectIds": [str(row["id"]) for row in rows],
                      "location": loc_t[t1] if conflict.theirs else loc_b[conflict.base_range[0]],
                      "base": {"markdown": _joined(conflict.base)},
                      "mine": {"markdown": _joined(conflict.mine)} if conflict.mine else {"deleted": True, "markdown": ""},
                      "theirsRevisions": {str(row["id"]): int(row["revision"]) for row in rows},
                      "afterObjectId": str(live[t1 - 1]["id"]) if live is not None and t1 > 0 else None}
        if rows:
            item["theirs"] = _theirs_info(connection, document_id, rows, names, {block.body for block in conflict.base}, actor)
        elif conflict.theirs:
            item["theirs"] = {"markdown": _joined(conflict.theirs), "revision": None, "deleted": False,
                              "updatedBy": None, "updatedAt": None}
        else:
            gone = _deleted_object(connection, document_id, conflict.base[0].body, base_revision) if conflict.base else None
            item["theirs"] = {"markdown": _joined(conflict.base), "deleted": True,
                              "revision": None if gone is None else int(gone["revision"]),
                              "deletedBy": None if gone is None else _person(gone["actor_id"], gone["name"]),
                              "deletedAt": None if gone is None else format_timestamp(gone["deleted_at"])}
            if gone is not None:
                item["objectIds"] = [str(gone["id"])]
        items.append(item)
    return items


def _public_item(item: dict, row) -> dict:
    value = {key: item[key] for key in ("itemIndex", "reason", "objectIds", "location", "opIndexes", "base", "theirs", "mine")
             if key in item}
    value["attempt"] = int(row["attempt"]) if row is not None else item.get("attempt", 1)
    if row is not None:
        value["status"] = row["status"]
        value["action"] = row["action"]
    return value


def _latest_theirs(payload: dict, index: int) -> dict | None:
    for attempt in reversed(payload.get("attempts", [])):
        if index in attempt.get("items", {}) or str(index) in attempt.get("items", {}):
            return attempt["items"].get(str(index)) or attempt["items"].get(index)
    return None


def _latest_snapshots(connection, conflict_id) -> dict[int, dict]:
    """方案 C：每个条目最新的一份“我的版本”快照（只追加表，按写入顺序取最后一份）。"""
    rows = connection.execute(
        "SELECT id, item_index, markdown, created_at, tab_id FROM document_conflict_mine_snapshots WHERE conflict_id=%s "
        "ORDER BY rowid", (str(conflict_id),)).fetchall()
    return {int(row["item_index"]): {"id": str(row["id"]), "markdown": row["markdown"], "savedAt": format_timestamp(row["created_at"]),
                                     "tabId": None if row["tab_id"] is None else str(row["tab_id"])} for row in rows}


def _other_tab_snapshots(connection, conflict_id, item_index: int, latest: dict | None) -> list[dict]:
    """审查建议 1（A3c）：其他标签页各自最后一份快照（与最新一份不同的）。最新一份若来自本标签页，
    另一标签页先写的字可能不在里面——前端处理前比对，没包含就确认并预填手动合并，不静默丢弃。"""
    rows = connection.execute(
        "SELECT tab_id, markdown, created_at FROM document_conflict_mine_snapshots "
        "WHERE conflict_id=%s AND item_index=%s AND tab_id IS NOT NULL ORDER BY rowid", (str(conflict_id), item_index)).fetchall()
    last: dict[str, dict] = {}
    for row in rows:
        last[str(row["tab_id"])] = {"tabId": str(row["tab_id"]), "markdown": row["markdown"], "savedAt": format_timestamp(row["created_at"])}
    latest_tab = (latest or {}).get("tabId")
    latest_md = (latest or {}).get("markdown")
    return [value for tab, value in last.items() if tab != latest_tab and value["markdown"] != latest_md]


def _snapshot_changed(conflict_id, index: int, payload: dict, latest: dict | None) -> AppError:
    return AppError(409, "MINE_SNAPSHOT_CHANGED", "你的版本刚有更新，请确认最新内容后再处理。", details={
        "conflictId": str(conflict_id), "itemIndex": index,
        "mineLatest": _mine_latest(next((entry for entry in payload["conflicts"] if int(entry["itemIndex"]) == index), {}), latest)})


def _carry_plan(connection, conflict_id, body) -> dict[int, dict]:
    """测试工程师 N3：处理请求带上的最新“我的版本”（mineMarkdown，副本到上限时编辑区 / 草稿里没存上的那份）。
    代码审查 M7：这里只读——核对客户端看到的快照 id（不一致 409 MINE_SNAPSHOT_CHANGED），不写快照；
    快照在 resolve 自己的写事务里、确认最新一份仍是客户端看到的那份之后才写，resolve 失败就一起回滚（不能借失败的请求绕过上限），
    别的标签页在此期间存了新快照时也 409，不把用户确认的内容静默换掉。返回 {条目下标: {markdown, key, seen}}。"""
    items = [item for item in body.items if item.mineMarkdown is not None
             and item.action in {"keep_mine", "keep_both", "restore_as_new"}]
    if not items:
        return {}
    if connection.in_transaction:
        connection.commit()
    state = {int(row["item_index"]): row["status"] for row in connection.execute(
        "SELECT item_index, status FROM document_conflict_items WHERE conflict_id=%s", (str(conflict_id),)).fetchall()}
    latest = _latest_snapshots(connection, conflict_id)
    payload = json.loads(connection.execute("SELECT payload_json FROM document_conflicts WHERE id=%s",
                                            (str(conflict_id),)).fetchone()["payload_json"])
    plan: dict[int, dict] = {}
    for item in items:
        if state.get(item.itemIndex) != "open":
            continue  # 由后面的“已处理 / 重放”分支回应
        now_id = (latest.get(item.itemIndex) or {}).get("id") or ""
        # 带了内容就必须核对：没给 id（null）按“客户端没见过任何快照”处理，不能当作不核对（审查 M7 并发 20 轮）
        seen = item.mineSnapshotId or ""
        if seen != now_id:
            raise _snapshot_changed(conflict_id, item.itemIndex, payload, latest.get(item.itemIndex))
        plan[item.itemIndex] = {"markdown": item.mineMarkdown, "key": f"resolve:{body.clientSaveKey}:{item.itemIndex}",
                                "seen": seen}
    return plan


def _mine_latest(item: dict, snapshot: dict | None) -> dict:
    """mineLatest：有快照用最新快照，没有时就是原始 mine（原始 mine 字段本身不变）。"""
    if snapshot is not None:
        return {"markdown": snapshot["markdown"], "savedAt": snapshot["savedAt"], "tabId": snapshot["tabId"],
                "snapshotId": snapshot["id"]}
    return {"markdown": "" if item.get("mine", {}).get("deleted") else item.get("mine", {}).get("markdown", ""),
            "savedAt": None, "tabId": None, "snapshotId": None}


def _items_view(connection, conflict_id: UUID, payload: dict, only_open: bool = False) -> list[dict]:
    rows = {int(row["item_index"]): row for row in connection.execute(
        "SELECT item_index, attempt, status, action FROM document_conflict_items WHERE conflict_id=%s", (conflict_id,)).fetchall()}
    result = []
    latest = _latest_snapshots(connection, conflict_id)
    for item in payload.get("conflicts", []):
        row = rows.get(int(item["itemIndex"]))
        if only_open and row is not None and row["status"] != "open":
            continue
        view = _public_item(item, row)
        view["mineLatest"] = _mine_latest(item, latest.get(int(item["itemIndex"])))
        refreshed = _latest_theirs(payload, int(item["itemIndex"]))
        if refreshed:
            view["theirs"] = refreshed["theirs"]
            view["objectIds"] = refreshed.get("objectIds", view["objectIds"])
        result.append(view)
    return result


def conflict_error(connection, conflict_id: UUID) -> AppError:
    row = connection.execute("SELECT payload_json, base_revision FROM document_conflicts WHERE id=%s", (conflict_id,)).fetchone()
    payload = json.loads(row["payload_json"])
    revision = connection.execute("SELECT d.revision FROM documents d JOIN document_conflicts c ON c.document_id=d.id "
                                  "WHERE c.id=%s", (conflict_id,)).fetchone()["revision"]
    items = _items_view(connection, conflict_id, payload, only_open=True)
    return AppError(409, "OBJECT_CONFLICT", f"有 {len(items) or 1} 处内容已被他人修改，你的修改已保留，请选择处理方式。",
                    current_revision=int(revision),
                    details={"conflictId": str(conflict_id), "baseRevision": int(row["base_revision"]), "conflicts": items,
                             "appliableOps": [], "appliableMarkdown": payload.get("appliableMarkdown"),
                             "appliableBaseRevision": payload.get("docRevision")})


def _rollup(connection, conflict_id: UUID) -> str:
    rows = connection.execute("SELECT status FROM document_conflict_items WHERE conflict_id=%s", (conflict_id,)).fetchall()
    statuses = {row["status"] for row in rows}
    status = "open" if "open" in statuses else "resolved" if "resolved" in statuses else "discarded"
    connection.execute("UPDATE document_conflicts SET status=%s, resolved_at=CASE WHEN %s='open' THEN NULL ELSE now() END "
                       "WHERE id=%s", (status, status, conflict_id))
    return status


def store_conflict(connection, *, document_id: UUID, actor: UUID, base_revision: int, doc_revision: int, source: str,
                   rhash: str, mine_md: str, items: list[dict], appliable_md: str | None,
                   frozen: list[tuple[str, int, str]] | None = None) -> AppError:
    """独立短事务写冲突副本（按 request_hash 去重），然后返回 409。"""
    if connection.in_transaction:
        connection.commit()
    payload = {"source": source, "baseRevision": base_revision, "docRevision": doc_revision, "mineMarkdown": mine_md,
               "conflicts": items, "attempts": [], "appliableMarkdown": appliable_md}
    with write_transaction(connection):
        existing = connection.execute(
            "SELECT id FROM document_conflicts WHERE document_id=%s AND actor_id=%s AND request_hash=%s AND status='open'",
            (document_id, actor, rhash)).fetchone()
        if existing is not None:
            conflict_id = existing["id"]
        else:
            conflict_id = uuid4()
            connection.execute(
                """INSERT INTO document_conflicts (id, document_id, actor_id, base_revision, doc_revision, source,
                       request_hash, payload_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())""",
                (conflict_id, document_id, actor, base_revision, doc_revision, source, rhash,
                 json.dumps(payload, ensure_ascii=False)),
            )
            for item in items:
                ref = item["objectIds"][0] if item["objectIds"] else None
                connection.execute(
                    """INSERT INTO document_conflict_items (conflict_id, item_index, reason, object_ref, location_json)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (conflict_id, item["itemIndex"], item["reason"], ref, json.dumps(item["location"], ensure_ascii=False)),
                )
                if ref is None:
                    continue
                # 同一人对同一对象的旧冲突被新的取代（内容仍在旧副本里，可在“已处理”查看）
                older = connection.execute(
                    """SELECT i.conflict_id FROM document_conflict_items i JOIN document_conflicts c ON c.id=i.conflict_id
                       WHERE c.document_id=%s AND c.actor_id=%s AND i.object_ref=%s AND i.status='open' AND i.conflict_id<>%s""",
                    (document_id, actor, ref, conflict_id)).fetchall()
                for row in older:
                    connection.execute(
                        """UPDATE document_conflict_items SET status='discarded', action='superseded', resolved_at=now(),
                           resolved_by=%s WHERE conflict_id=%s AND object_ref=%s AND status='open'""",
                        (actor, row["conflict_id"], ref))
                    _rollup(connection, row["conflict_id"])
        limited = _append_frozen(connection, actor, rhash, frozen) if frozen else []
    error = conflict_error(connection, conflict_id)
    if frozen:
        error.details["frozenByConflict"] = sorted({cid for cid, _index, _mine in frozen})
    if limited:
        error.details["snapshotLimited"] = limited
    return error


# ---------------------------------------------------------------- 方案 C：冲突段冻结 + “我的版本”快照

def append_snapshot(connection, *, conflict_id, item_index: int, actor, markdown: str, key: str,
                    tab_id: str | None = None, source: str = "editor", seq: int | None = None) -> dict:
    """只追加一份快照（须在写事务内调用）。同键同内容返回已有那份；同键不同内容 422；与最新一份相同则不重复写。"""
    existing = connection.execute(
        "SELECT id, item_index, markdown, created_at, tab_id FROM document_conflict_mine_snapshots "
        "WHERE conflict_id=%s AND client_save_key=%s", (str(conflict_id), key)).fetchone()
    if existing is not None:
        if int(existing["item_index"]) != item_index or existing["markdown"] != markdown:
            raise AppError(422, "IDEMPOTENCY_KEY_REUSED", "保存键已用于另一份内容，请重新保存。")
        return {"id": str(existing["id"]), "markdown": existing["markdown"], "savedAt": format_timestamp(existing["created_at"]),
                "tabId": None if existing["tab_id"] is None else str(existing["tab_id"]), "replayed": True}
    latest = _latest_snapshots(connection, conflict_id).get(item_index)
    if latest is not None and latest["markdown"] == markdown and seq is None:
        # 带序号的照样追加一行（内容相同）：记下这个序号，之后乱序到达的更小序号才能被识别为旧快照
        return {**latest, "unchanged": True}
    snapshot_id = str(uuid4())
    connection.execute(
        """INSERT INTO document_conflict_mine_snapshots (id, conflict_id, item_index, actor_id, markdown, client_save_key,
               tab_id, source, seq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (snapshot_id, str(conflict_id), item_index, str(actor), markdown, key, tab_id, source, seq))
    row = connection.execute("SELECT created_at FROM document_conflict_mine_snapshots WHERE id=%s", (snapshot_id,)).fetchone()
    return {"id": snapshot_id, "markdown": markdown, "savedAt": format_timestamp(row["created_at"]), "tabId": tab_id}


def _frozen_matches(connection, document_id: UUID, actor, items: list[dict]) -> dict[int, tuple[str, int]]:
    """方案 C 后端第 4 条：本人对同一段已有未处理冲突时，这一段算“冻结”，不新建冲突（也不产生 superseded）。
    返回 {新条目下标: (已有冲突 id, 已有条目下标)}。"""
    found: dict[int, tuple[str, int]] = {}
    for position, item in enumerate(items):
        ids = [str(value) for value in item.get("objectIds") or []]
        if not ids:
            continue
        rows = connection.execute(
            """SELECT i.conflict_id, i.item_index, c.payload_json FROM document_conflict_items i
               JOIN document_conflicts c ON c.id=i.conflict_id
               WHERE c.document_id=%s AND c.actor_id=%s AND i.status='open'
                 AND i.object_ref IN (SELECT value FROM json_each(%s))
               ORDER BY c.created_at DESC""",
            (document_id, str(actor), json.dumps(ids))).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            index = int(row["item_index"])
            old = next((entry for entry in payload.get("conflicts", []) if int(entry["itemIndex"]) == index), None)
            if old is None:
                continue
            known = set(old.get("objectIds") or []) | set((_latest_theirs(payload, index) or {}).get("objectIds") or [])
            # 只有新冲突区间完全落在原冲突段里才算同一段（连带了别的段时照常新建冲突）
            if set(ids) <= {str(value) for value in known}:
                found[position] = (str(row["conflict_id"]), index)
                break
    return found


def _frozen_mine(item: dict) -> str:
    return "" if item.get("mine", {}).get("deleted") else item.get("mine", {}).get("markdown", "")


# ---------------------------------------------------------------- 整篇合并提交

BASE_MISMATCH_MIN_BLOCKS = 4
BASE_MISMATCH_MIN_SHARE = 0.3


def _check_explicit_base(connection, document_id: UUID, base_revision: int, base_markdown: str) -> None:
    """增量审查建议 1(b)：显式基准与 expectedRevision 那一版明显对不上（空基准、或几乎没有共同段落）时 409，
    不静默按它合并（前端出错时会把整段当成新增，造成重复）。基准是“上次保存对应的编辑区文字”，与该版只差
    合并进来但没显示的少量他人修改，正常情况下共同段落占绝大多数。"""
    version = _version_markdown(connection, document_id, base_revision)
    if version is None or base_markdown == version:
        return
    _, version_blocks = split(version)
    _, base_blocks = split(base_markdown)
    if not version_blocks:
        return
    mismatch = not base_blocks
    if not mismatch and len(version_blocks) >= BASE_MISMATCH_MIN_BLOCKS:
        pool = Counter(block.body for block in version_blocks)
        shared = sum((pool & Counter(block.body for block in base_blocks)).values())
        mismatch = shared < BASE_MISMATCH_MIN_SHARE * len(version_blocks)
    if mismatch:
        raise AppError(409, "BASE_MISMATCH", "保存所依据的内容与服务器上的版本对不上，已停止保存，请刷新后再试（你的修改保留在本机）。",
                       details={"baseRevision": base_revision})


def commit_whole(connection, *, document_id: UUID, actor: UUID, base_revision: int, mine_md: str, kind: str,
                 client_save_key: str | None, check_word: Callable[[str], None], source: str = "content",
                 extra: dict | None = None, after_write: Callable | None = None,
                 template_update: Callable[[dict], dict] | None = None, base_markdown: str | None = None) -> dict:
    """allowMerge 的整篇提交；返回 {revision, merged, changeId, changedBy, markdownContent?, noop?}。

    base_markdown：客户端给出的显式基准正文（增量审查 M1），代替 versions[base_revision] 作三方合并的基线。
    """
    from app.documents import _save_markdown

    explicit = {"baseMarkdown": base_markdown} if base_markdown is not None else {}
    rhash = request_hash({"base": base_revision, "markdown": mine_md, "kind": kind, **(extra or {}), **explicit})
    replayed = replay(connection, document_id, actor, client_save_key, rhash)
    if replayed is not None:
        return replayed
    base_md = base_markdown if base_markdown is not None else _version_markdown(connection, document_id, base_revision)
    if base_markdown is not None:
        _check_explicit_base(connection, document_id, base_revision, base_markdown)
    for attempt in range(OPTIMISTIC_ATTEMPTS + 1):
        guard = document_lock(document_id) if attempt == OPTIMISTIC_ATTEMPTS else nullcontext()
        with guard:
            if connection.in_transaction:
                connection.commit()
            snap = connection.execute("SELECT revision, markdown_content FROM documents WHERE id=%s", (document_id,)).fetchone()
            revision, theirs = int(snap["revision"]), snap["markdown_content"] or ""
            if base_revision > revision:
                raise AppError(422, "VALIDATION_FAILED", "保存所依据的版本不存在。")
            if base_md is None:
                if base_revision != revision:
                    raise AppError(409, "BASE_REVISION_UNAVAILABLE", "找不到保存所依据的版本，请刷新并合并。",
                                   current_revision=revision)
                base_md = theirs
            merged_flag = revision != base_revision or (base_markdown is not None and base_markdown != theirs)
            frozen: list[tuple[str, int, str]] = []
            if merged_flag:
                ensure_committed(connection, document_id)
                others = connection.execute(
                    """SELECT COUNT(*) AS n FROM document_changes WHERE document_id=%s AND doc_revision > %s
                       AND kind NOT IN ('backfill','realign') AND (actor_id IS NULL OR actor_id <> %s)""",
                    (document_id, base_revision, actor)).fetchone()["n"]
                # 增量审查建议 2：带显式基准时 others（从 base_revision 起算）恒为 0，不能据此去重——
                # 显式基准与正文的差异里可能有别人的修改（合并进来但编辑区没显示），按修订差判断：只有修订没变才去重
                dedupe = others == 0 if base_markdown is None else revision == base_revision and base_markdown == _version_markdown(
                    connection, document_id, base_revision)
                result = merge3(base_md, theirs, mine_md, dedupe_inserts=dedupe)
                if result.table_rows_merged:
                    # 设计师复验 2 C：按行合并出的表若通不过 Word 校验（含合并单元格的表，同一合并区两边各改一行），
                    # 不退为整篇 FORMAT 冲突——关掉按行合并重来，这张表按整表冲突处理，别处的修改照常进正文
                    try:
                        check_word(result.markdown)
                    except AppError as error:
                        if error.code not in WORD_CODES:
                            raise
                        own = _own_word_error(check_word, base_md, mine_md)
                        if own is not None:
                            raise own from None
                        result = merge3(base_md, theirs, mine_md, dedupe_inserts=dedupe, table_rows=False)
                if result.conflicts or result.prefix_conflict:
                    if not result.conflicts:
                        result.conflicts = []
                    items = build_items(connection, document_id, base_md, theirs, result, base_revision, actor)
                    if not items:
                        items = [_whole_item(0, "STRUCTURE", mine_md, theirs, base_md, _whole_by(connection, document_id, base_revision, actor))]
                    matches = _frozen_matches(connection, document_id, actor, items)
                    frozen = [(cid, index, _frozen_mine(items[position])) for position, (cid, index) in matches.items()]
                    remaining = [item for position, item in enumerate(items) if position not in matches]
                    if remaining or result.prefix_conflict:
                        for number, item in enumerate(remaining):
                            item["itemIndex"] = number
                        error = store_conflict(connection, document_id=document_id, actor=actor, base_revision=base_revision,
                                               doc_revision=revision, source=source, rhash=rhash, mine_md=mine_md,
                                               items=remaining or [_whole_item(0, "STRUCTURE", mine_md, theirs, base_md, _whole_by(connection, document_id, base_revision, actor))],
                                               appliable_md=result.markdown, frozen=frozen)
                        raise error
                merged = result.markdown
                whitespace_notes = result.whitespace_notes()
            else:
                merged = mine_md
                whitespace_notes = {}
            frozen_ids = sorted({cid for cid, _index, _mine in frozen})
            frozen_extra = {"frozenByConflict": frozen_ids} if frozen_ids else {}
            if merged == theirs:
                if frozen:
                    if connection.in_transaction:
                        connection.commit()
                    with write_transaction(connection):
                        limited = _append_frozen(connection, actor, rhash, frozen)
                    if limited:
                        frozen_extra = {**frozen_extra, "snapshotLimited": limited}
                return {"revision": revision, "merged": merged_flag, "changeId": None, "noop": True, **frozen_extra,
                        **whitespace_notes,
                        "changedBy": _changed_by(connection, document_id, base_revision, actor), "markdownContent": theirs}
            try:
                check_word(merged)
            except AppError as error:
                if error.code not in {"AI_FORMAT_INVALID", "WORD_BLOCK_PROTECTED"} or not merged_flag:
                    raise
                # 设计师复验 2 C：自己这次的修改本身就通不过 Word 校验（如改了合并单元格）——按单人路径直接 422，
                # 界面给［撤销这一段的修改］，不退为整篇 FORMAT 冲突把别处的修改也撤出正文
                own = _own_word_error(check_word, base_md, mine_md)
                if own is not None:
                    raise own from None
                item = _whole_item(0, "FORMAT", mine_md, theirs, base_md, _whole_by(connection, document_id, base_revision, actor))
                if error.details and error.details.get("location"):
                    item["location"] = error.details["location"]
                raise store_conflict(connection, document_id=document_id, actor=actor, base_revision=base_revision,
                                     doc_revision=revision, source=source, rhash=rhash, mine_md=mine_md, items=[item],
                                     appliable_md=theirs) from None
            if connection.in_transaction:
                connection.commit()
            try:
                with write_transaction(connection):
                    current = connection.execute("SELECT revision FROM documents WHERE id=%s", (document_id,)).fetchone()
                    if int(current["revision"]) != revision:
                        raise _Retry()
                    template = None
                    if template_update is not None:
                        # S6：图表/测试结果在写锁内基于当前 format_template 追加，不覆盖他人同时写入的图片。
                        raw = connection.execute("SELECT format_template FROM documents WHERE id=%s", (document_id,)).fetchone()[0]
                        template = template_update(dict(json.loads(raw) if isinstance(raw, str) else (raw or {})))
                    change_id = _save_markdown(connection, document_id, merged, revision, kind=kind, merged=merged_flag,
                                               client_save_key=client_save_key, request_hash=rhash, format_template=template)
                    if after_write is not None:
                        after_write(connection, revision + 1)
                    data = {"revision": revision + 1, "merged": merged_flag, "changeId": str(change_id),
                            "changedBy": _changed_by(connection, document_id, base_revision, actor)}
                    data.update(whitespace_notes)
                    if frozen:
                        limited = _append_frozen(connection, actor, rhash, frozen)
                        data.update(frozen_extra)
                        if limited:
                            data["snapshotLimited"] = limited
                    connection.execute("UPDATE document_changes SET result_json=%s WHERE id=%s",
                                       (json.dumps(data, ensure_ascii=False), change_id))
            except _Retry:
                continue
            except sqlite3.IntegrityError:
                again = replay(connection, document_id, actor, client_save_key, rhash)
                if again is not None:
                    return again
                raise
            data["markdownContent"] = merged
            return data
    raise AppError(503, "DATABASE_BUSY", "文档正在被频繁修改，请稍后重试。", retry_after=1)


WORD_CODES = frozenset({"AI_FORMAT_INVALID", "WORD_BLOCK_PROTECTED"})


def _own_word_error(check_word, base_md: str, mine_md: str) -> AppError | None:
    """设计师复验 2 C：我这次的修改本身（不含别人的）能否写回原 Word；不能时返回那个 422（单人路径，界面给［撤销这一段的修改］）。"""
    if mine_md == base_md:
        return None
    try:
        check_word(mine_md)
    except AppError as own:
        if own.code in WORD_CODES:
            return own
        raise
    return None


def _snapshot_limit_hit(connection, conflict_id, item_index: int, nbytes: int) -> str | None:
    """快照份数 / 总大小上限（/mine 与 resubmit 共用）：超了返回原因（"count" / "bytes"），否则 None。"""
    if connection.execute(
            "SELECT COUNT(*) AS n FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND item_index=%s",
            (str(conflict_id), item_index)).fetchone()["n"] >= MAX_SNAPSHOTS_PER_ITEM:
        return "count"
    used = connection.execute(
        "SELECT COALESCE(SUM(LENGTH(CAST(markdown AS BLOB))), 0) AS n FROM document_conflict_mine_snapshots WHERE conflict_id=%s",
        (str(conflict_id),)).fetchone()["n"]
    return "bytes" if used + nbytes > MAX_SNAPSHOT_BYTES_PER_CONFLICT else None


def _append_frozen(connection, actor, rhash: str, frozen: list[tuple[str, int, str]]) -> list[dict]:
    """冻结段里提交上来的“我的”内容追加为快照（原冲突不变、不新建冲突）。
    审查建议 4：同样受 500 份 / 20MB 上限约束（之前从旧修订反复重交可绕过）；超限的不追加（正文其余部分照常保存），
    返回 [{conflictId, itemIndex, markdown}] 由调用方放进响应 snapshotLimited，前端提示先处理冲突。"""
    limited: list[dict] = []
    for cid, index, markdown in frozen:
        key = f"resubmit:{rhash[:48]}:{index}"
        replay = connection.execute("SELECT 1 FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND client_save_key=%s",
                                    (str(cid), key)).fetchone()
        latest = _latest_snapshots(connection, cid).get(index)
        if replay is None and not (latest is not None and latest["markdown"] == markdown) \
                and _snapshot_limit_hit(connection, cid, index, len(markdown.encode("utf-8"))):
            # 测试工程师 N3：带上没存进去的内容——编辑区里没有这一段时（恢复草稿等）前端据此保留，处理时作为 mineMarkdown 带上
            limited.append({"conflictId": str(cid), "itemIndex": index, "markdown": markdown})
            continue
        append_snapshot(connection, conflict_id=cid, item_index=index, actor=actor, markdown=markdown, key=key, source="resubmit")
    return limited


def _whole_by(connection, document_id: UUID, base_revision: int, actor) -> dict:
    """设计师复验 2 C：整篇冲突（FORMAT / STRUCTURE）也写明“对方”——基准之后最近一次由别人做的修改；没有就留空（界面写“正文当前版本”）。"""
    row = connection.execute(
        """SELECT c.actor_id, u.name, c.created_at FROM document_changes c LEFT JOIN users u ON u.id=c.actor_id
           WHERE c.document_id=%s AND c.doc_revision > %s AND c.actor_id IS NOT NULL AND c.actor_id<>%s
             AND c.kind NOT IN ('backfill','realign')
           ORDER BY c.doc_revision DESC LIMIT 1""", (document_id, base_revision, actor)).fetchone()
    if row is None:
        return {"updatedBy": None, "updatedAt": None}
    return {"updatedBy": _person(row["actor_id"], row["name"]), "updatedAt": format_timestamp(row["created_at"])}


def _whole_item(index: int, reason: str, mine_md: str, theirs_md: str, base_md: str, by: dict | None = None) -> dict:
    return {"itemIndex": index, "attempt": 1, "reason": reason, "opIndexes": [], "objectIds": [],
            "location": {"headingPath": [], "ordinal": None, "kind": "other", "label": None},
            "base": {"markdown": base_md}, "theirs": {"markdown": theirs_md, "deleted": False, "revision": None, **(by or {})},
            "mine": {"markdown": mine_md}, "theirsRevisions": {}, "afterObjectId": None}


# ---------------------------------------------------------------- 查询（4.4.2）

def _conflict_access(connection, request: Request, document_id: str, need: str = "read"):
    principal = document_principal(request)
    try:
        did = UUID(document_id)
    except ValueError as exc:
        raise AppError(404, "NOT_FOUND", "文档不存在或你无权访问。") from exc
    row = load_document(connection, did, principal.user_id, is_admin(principal), need)
    manager = row.admin or row.project_role == "owner"
    return principal, did, row, manager


def _item_row(connection, conflict, item: dict, row, latest: dict[int, dict] | None = None) -> dict:
    value = _public_item(item, row)
    if latest is None:
        latest = _latest_snapshots(connection, conflict["id"])
    value["mineLatest"] = _mine_latest(item, latest.get(int(item["itemIndex"])))
    value["mineOthers"] = _other_tab_snapshots(connection, conflict["id"], int(item["itemIndex"]), latest.get(int(item["itemIndex"])))
    refreshed = _latest_theirs(json.loads(conflict["payload_json"]), int(item["itemIndex"]))
    if refreshed:
        value["theirs"] = refreshed["theirs"]
    value.update(conflictId=str(conflict["id"]), actor=_person(conflict["actor_id"], conflict["name"]),
                 createdAt=format_timestamp(conflict["created_at"]),
                 resolvedAt=format_timestamp(row["resolved_at"]) if row["resolved_at"] else None)
    return value


@router.get("/documents/{document_id}/conflicts")
def list_conflicts(request: Request, document_id: str, status: str = Query(default="open", pattern="^(open|resolved|all)$"),
                   actor: str = Query(default="me", pattern="^(me|all)$")) -> dict:
    with connect() as connection:
        principal, did, _row, manager = _conflict_access(connection, request, document_id)
        if actor == "all" and not manager:
            raise AppError(403, "FORBIDDEN", "只有项目负责人和系统管理员可以查看所有成员的冲突。")
        sql = """SELECT c.id, c.actor_id, u.name, c.created_at, c.payload_json, i.item_index, i.status, i.action, i.attempt,
                        i.resolved_at
                 FROM document_conflict_items i JOIN document_conflicts c ON c.id=i.conflict_id
                 LEFT JOIN users u ON u.id=c.actor_id WHERE c.document_id=%s"""
        params: list = [did]
        if actor == "me":
            sql += " AND c.actor_id=%s"
            params.append(principal.user_id)
        if status == "open":
            sql += " AND i.status='open'"
        elif status == "resolved":
            sql += " AND i.status<>'open'"
        sql += " ORDER BY c.created_at DESC, i.item_index"
        rows = connection.execute(sql, params).fetchall()
        payloads: dict[str, dict] = {}
        snapshots: dict[str, dict[int, dict]] = {}
        items = []
        for row in rows:
            payload = payloads.setdefault(str(row["id"]), json.loads(row["payload_json"]))
            item = next(entry for entry in payload["conflicts"] if int(entry["itemIndex"]) == int(row["item_index"]))
            if str(row["id"]) not in snapshots:
                snapshots[str(row["id"])] = _latest_snapshots(connection, row["id"])
            items.append(_item_row(connection, row, item, row, snapshots[str(row["id"])]))
        return {"data": {"items": items}, "requestId": request.state.request_id}


def open_counts(connection, document_ids: list[UUID], user_id: UUID) -> dict[str, tuple[int, int]]:
    """文档列表用：一次 GROUP BY（4.4.2）。返回 {did: (mine, all)}。"""
    if not document_ids:
        return {}
    rows = connection.execute(
        """SELECT c.document_id, SUM(CASE WHEN c.actor_id=%s THEN 1 ELSE 0 END) AS mine, COUNT(*) AS total
           FROM document_conflict_items i JOIN document_conflicts c ON c.id=i.conflict_id
           WHERE i.status='open' AND c.document_id IN (SELECT value FROM json_each(%s)) GROUP BY c.document_id""",
        (user_id, json.dumps([str(value) for value in document_ids])),
    ).fetchall()
    return {str(row["document_id"]): (int(row["mine"] or 0), int(row["total"])) for row in rows}


@router.get("/documents/{document_id}/conflicts/summary")
def conflict_summary(request: Request, document_id: str) -> dict:
    with connect() as connection:
        principal, did, _row, manager = _conflict_access(connection, request, document_id)
        mine, total = open_counts(connection, [did], principal.user_id).get(str(did), (0, 0))
        # 界面验收 M2：全部处理后仍要能进入“已处理”，所以另给本人已处理条数。
        handled = connection.execute(
            """SELECT COUNT(*) AS n FROM document_conflict_items i JOIN document_conflicts c ON c.id=i.conflict_id
               WHERE c.document_id=%s AND c.actor_id=%s AND i.status<>'open'""", (did, principal.user_id)).fetchone()["n"]
        return {"data": {"mineOpen": mine, "mineHandled": int(handled), "allOpen": total if manager else None},
                "requestId": request.state.request_id}


def _load_conflict(connection, did: UUID, conflict_id: str):
    try:
        cid = UUID(conflict_id)
    except ValueError as exc:
        raise AppError(404, "NOT_FOUND", "没有这条冲突记录。") from exc
    row = connection.execute(
        """SELECT c.*, u.name FROM document_conflicts c LEFT JOIN users u ON u.id=c.actor_id
           WHERE c.id=%s AND c.document_id=%s""", (cid, did)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这条冲突记录。")
    return row


@router.get("/documents/{document_id}/conflicts/{conflict_id}")
def get_conflict(request: Request, document_id: str, conflict_id: str) -> dict:
    with connect() as connection:
        principal, did, _row, manager = _conflict_access(connection, request, document_id)
        conflict = _load_conflict(connection, did, conflict_id)
        if str(conflict["actor_id"]) != str(principal.user_id) and not manager:
            raise AppError(403, "FORBIDDEN", "冲突副本只有本人、项目负责人和系统管理员可以查看。")
        payload = json.loads(conflict["payload_json"])
        rows = connection.execute("SELECT * FROM document_conflict_items WHERE conflict_id=%s ORDER BY item_index",
                                  (conflict["id"],)).fetchall()
        return {"data": {"conflictId": str(conflict["id"]), "status": conflict["status"],
                         "actor": _person(conflict["actor_id"], conflict["name"]),
                         "baseRevision": int(conflict["base_revision"]), "docRevision": int(conflict["doc_revision"]),
                         "createdAt": format_timestamp(conflict["created_at"]), "source": conflict["source"],
                         "mineMarkdown": payload.get("mineMarkdown"), "payload": payload,
                         "items": [_item_row(connection, conflict, item, row) for item, row in zip(
                             sorted(payload["conflicts"], key=lambda entry: int(entry["itemIndex"])), rows)]},
                "requestId": request.state.request_id}


# ---------------------------------------------------------------- 处理并提交（4.4.3）

class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(after_nearest_live|section_end|after)$")
    afterObjectId: str | None = None


class ResolveItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itemIndex: int = Field(ge=0)
    action: str = Field(pattern="^(keep_mine|use_theirs|manual|keep_both|restore_as_new|discard)$")
    theirsRevision: int | None = None
    markdown: str | None = None
    placement: Placement | None = None
    # 审查点 3：客户端看到（并据此确认）的“我的版本”快照 id；""＝没有快照（原始版本）。与服务器最新一份不一致时 409
    mineSnapshotId: str | None = Field(default=None, max_length=64)
    # 测试工程师 N3（用户已定）：副本到上限后编辑区 / 本机草稿里还有没存进服务器的最新“我的版本”——随处理请求带上，
    # 只用于这次处理（记为一份快照以便“已处理”里下载，不受 500 份 / 20MB 上限约束）
    mineMarkdown: str | None = Field(default=None, max_length=2_000_000)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientSaveKey: str = Field(min_length=8, max_length=80)
    baseDocumentRevision: int = Field(ge=0)
    items: list[ResolveItem] = Field(min_length=1, max_length=200)


_ALLOWED = {
    "MODIFIED": {"keep_mine", "use_theirs", "manual", "keep_both"},
    "STRUCTURE": {"keep_mine", "use_theirs"},
    "FORMAT": {"use_theirs"},
    "DELETED": {"restore_as_new", "discard", "keep_mine", "use_theirs"},
    "ANCHOR_LOST": {"restore_as_new", "discard"},
}


class _Reconflict(Exception):
    def __init__(self, indexes: list[int]):
        self.indexes = indexes


def _apply_actions(connection, did: UUID, payload: dict, request_items: list[ResolveItem],
                   latest: dict[int, dict] | None = None) -> tuple[str, list[dict], bool]:
    """在当前存活对象上应用处理动作；返回 (新全文, 每项结果, 是否写正文)。再次冲突抛 _Reconflict。"""
    doc = connection.execute("SELECT objects_prefix, markdown_content FROM documents WHERE id=%s", (did,)).fetchone()
    live = list(live_objects(connection, did))
    entries: list[Entry] = [Entry(Block(row["kind"], row["body"], row["gap_after"], row["heading_level"]), "t", index)
                            for index, row in enumerate(live)]
    ids: list[str | None] = [str(row["id"]) for row in live]
    live_by_id = {str(row["id"]): row for row in live}
    by_index = {int(item["itemIndex"]): item for item in payload["conflicts"]}
    reconflicts: list[int] = []
    results: list[dict] = []
    writes = False

    def position_of(object_id: str | None) -> int | None:
        return ids.index(object_id) if object_id in ids else None

    def by_location(location: dict | None) -> tuple[int, bool]:
        """按章节路径 + 同类第 N 块定位（返回插入下标, 是否只能放到章节/文档末尾）。"""
        location = location or {}
        path = [str(value) for value in (location.get("headingPath") or [])]
        kind, ordinal = location.get("kind"), location.get("ordinal")
        stack: list[tuple[int, str]] = []
        start: int | None = 0 if not path else None
        for index, entry in enumerate(entries):
            if entry.block.kind == "heading" and entry.block.heading_level:
                level = entry.block.heading_level
                stack = [item for item in stack if item[0] < level]
                stack.append((level, heading_text(entry.block.body).strip()))
                if start is None and [text for _, text in stack] == path:
                    start = index + 1
        if start is None and path:
            # 路径对不上（上级标题改名等）时按最后一级标题名找
            start = next((index + 1 for index, entry in enumerate(entries) if entry.block.kind == "heading"
                          and heading_text(entry.block.body).strip() == path[-1]), None)
        if start is None:
            return len(entries), True
        end = start
        while end < len(entries) and entries[end].block.kind != "heading":
            end += 1
        if isinstance(ordinal, int) and ordinal >= 1 and kind and kind != "heading":
            if ordinal == 1:
                return start, False
            seen = 0
            for index in range(start, end):
                if entries[index].block.kind == kind:
                    seen += 1
                    if seen == ordinal - 1:
                        return index + 1, False
        # 本节内容比原来少：放到所属章节末尾（含下级小节之前的正文末尾），提示用户检查
        return end, True

    def nearest_live(object_id: str | None, location: dict | None = None) -> tuple[int, bool]:
        """复审 M3：按删除时记下的前驱对象定位；前驱也被删了就沿着它的前驱继续找。
        找不到（本功能上线前删除的对象等）时回退到章节路径 + 序号，再不行放到章节末尾并标记 placementFallback。"""
        current = object_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            row = connection.execute("SELECT deleted_at, deleted_after_ref FROM document_objects WHERE id=%s",
                                     (current,)).fetchone()
            if row is None:
                break
            if row["deleted_at"] is None and current != object_id:
                index = position_of(current)
                if index is not None:
                    return index + 1, False
                break
            after = row["deleted_after_ref"]
            if after is not None and str(after) == "^":
                return 0, False
            current = str(after) if after is not None else None
        return by_location(location)

    def insert_at(index: int, markdown: str) -> None:
        _, blocks = split(markdown)
        for offset, block in enumerate(blocks):
            entries.insert(index + offset, Entry(block, "n", 0))
            ids.insert(index + offset, None)

    for request_item in request_items:
        item = by_index.get(request_item.itemIndex)
        if item is None:
            raise AppError(422, "VALIDATION_FAILED", "冲突条目不存在。")
        reason, action = item["reason"], request_item.action
        if action not in _ALLOWED[reason]:
            raise AppError(422, "VALIDATION_FAILED", "这处冲突不能使用该处理方式。")
        if reason == "DELETED" and action == "keep_mine" and not item["mine"].get("deleted"):
            # 审查 S1：对方删、我改时“保留我的”就是把我的内容作为新段落恢复（原对象已删，不能按原位替换）
            action = "restore_as_new"
        refreshed = _latest_theirs(payload, request_item.itemIndex)
        object_ids = (refreshed or {}).get("objectIds", item["objectIds"])
        revisions = (refreshed or {}).get("theirsRevisions", item.get("theirsRevisions", {}))
        ref = object_ids[0] if object_ids else None
        mine = item["mine"].get("markdown", "") if not item["mine"].get("deleted") else ""
        result = {"itemIndex": request_item.itemIndex, "status": "discarded" if action in {"use_theirs", "discard"} else "resolved"}
        snapshot = (latest or {}).get(request_item.itemIndex)
        if snapshot is not None and not item["mine"].get("deleted"):
            # 方案 C：用最新一份“我的版本”快照（含冲突后续写），并记下用的是哪一份
            mine = snapshot["markdown"]
            result["mineSnapshotId"] = snapshot["id"]
            result["mineSavedAt"] = snapshot["savedAt"]
        if action in {"keep_mine", "keep_both", "restore_as_new"} and not item["mine"].get("deleted") and not mine.strip():
            raise AppError(422, "MINE_EMPTY", "你的版本已没有文字，请使用对方的版本或手动合并。",
                           details={"itemIndex": request_item.itemIndex})
        if action in {"use_theirs", "discard"}:
            results.append(result)
            continue
        if action == "manual":
            mine = (request_item.markdown or "").strip()
            if not mine:
                raise AppError(422, "VALIDATION_FAILED", "合并后的内容不能为空；如不需要这段内容，请使用对方的版本。")
        if action in {"keep_mine", "manual"}:
            if reason == "DELETED" and item["mine"].get("deleted"):
                # 我删除、对方修改：保留我的 = 删除对方版本（需对方未再修改）
                index = position_of(ref)
                if index is None:
                    results.append(result)
                    continue
                if int(live_by_id[ref]["revision"]) != revisions.get(ref, item["theirs"].get("revision")):
                    reconflicts.append(request_item.itemIndex)
                    continue
                del entries[index], ids[index]
                writes = True
                results.append(result)
                continue
            expected = request_item.theirsRevision if request_item.theirsRevision is not None else revisions.get(ref)
            positions = [position_of(object_id) for object_id in object_ids]
            changed = any(position is None for position in positions) or any(
                int(live_by_id[object_id]["revision"]) !=
                (expected if object_id == ref and expected is not None else revisions.get(object_id))
                for object_id in object_ids)
            if changed:
                reconflicts.append(request_item.itemIndex)
                continue
            if positions:
                start = min(p for p in positions if p is not None)
                for object_id in object_ids:
                    index = position_of(object_id)
                    del entries[index], ids[index]
            else:
                anchor = position_of(item.get("afterObjectId"))
                start = 0 if anchor is None else anchor + 1
            insert_at(start, mine)
            writes = True
            result["objectId"] = ref
            results.append(result)
            continue
        if action == "keep_both":
            index = position_of(ref)
            if index is not None:
                insert_at(index + 1, mine)
            else:
                target, fallback = nearest_live(ref, item.get("location"))
                if fallback:
                    result["placementFallback"] = True
                insert_at(target, mine)
            writes = True
            results.append(result)
            continue
        # restore_as_new
        placement = request_item.placement or Placement(mode="after_nearest_live")
        if placement.mode == "after":
            anchor = position_of(placement.afterObjectId)
            if anchor is None:
                raise AppError(422, "VALIDATION_FAILED", "选择的位置已不存在，请重新选择。")
            target = anchor + 1
        elif placement.mode == "section_end":
            path = item["location"].get("headingPath") or []
            target = None
            if path:
                title = path[-1]
                for index, entry in enumerate(entries):
                    if entry.block.kind == "heading" and heading_text(entry.block.body) == title:
                        level = entry.block.heading_level or 6
                        end = index + 1
                        while end < len(entries) and not (entries[end].block.kind == "heading"
                                                          and (entries[end].block.heading_level or 6) <= level):
                            end += 1
                        target = end
                        break
            if target is None:
                result["placementFallback"] = True
                target, _ = nearest_live(ref, item.get("location"))
        else:
            target, fallback = nearest_live(ref, item.get("location"))
            if fallback:
                result["placementFallback"] = True
        insert_at(target, mine)
        writes = True
        results.append(result)
    if reconflicts:
        raise _Reconflict(reconflicts)
    return assemble_entries(doc["objects_prefix"], entries), results, writes


def _refresh_attempt(connection, conflict, payload: dict, indexes: list[int]) -> None:
    """“保留我的”再次冲突：条目 attempt+1，attempts[] 追加最新 theirs（不改 mine）。"""
    live = {str(row["id"]): row for row in live_objects(connection, conflict["document_id"])}
    names = _names(connection, [row["updated_by"] for row in live.values()])
    snapshot: dict[str, dict] = {}
    for index in indexes:
        item = next(entry for entry in payload["conflicts"] if int(entry["itemIndex"]) == index)
        refreshed = _latest_theirs(payload, index) or {}
        # 审查 S1：保留原 objectIds 用于定位（已删的另记 goneObjectIds），不能过滤成空数组
        object_ids = list(refreshed.get("objectIds", item["objectIds"]))
        rows = [live[value] for value in object_ids if value in live]
        base_bodies = {block.body for block in split(item.get("base", {}).get("markdown") or "")[1]}
        theirs = _theirs_info(connection, conflict["document_id"], rows, names, base_bodies, conflict["actor_id"]) if rows else {
            "markdown": item["theirs"].get("markdown"), "deleted": True, "revision": None}
        snapshot[str(index)] = {"theirs": theirs, "objectIds": object_ids,
                                "goneObjectIds": [value for value in object_ids if value not in live],
                                "theirsRevisions": {str(row["id"]): int(row["revision"]) for row in rows}}
    payload.setdefault("attempts", []).append({"at": format_timestamp_now(connection), "items": snapshot})
    with write_transaction(connection):
        connection.execute("UPDATE document_conflicts SET payload_json=%s WHERE id=%s",
                           (json.dumps(payload, ensure_ascii=False), conflict["id"]))
        for index in indexes:
            connection.execute("UPDATE document_conflict_items SET attempt=attempt+1 WHERE conflict_id=%s AND item_index=%s",
                               (conflict["id"], index))


def format_timestamp_now(connection) -> str:
    return str(connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now') AS t").fetchone()["t"])


@router.post("/documents/{document_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(request: Request, document_id: str, conflict_id: str, body: ResolveBody) -> dict:
    from app.documents import _check_word, _save_markdown

    with connect() as connection:
        principal, did, row, _manager = _conflict_access(connection, request, document_id)
        conflict = _load_conflict(connection, did, conflict_id)
        if str(conflict["actor_id"]) != str(principal.user_id):
            raise AppError(403, "FORBIDDEN", "只有提交这份修改的人可以处理冲突。")
        row = load_document(connection, did, principal.user_id, is_admin(principal), "edit")
        indexes = [item.itemIndex for item in body.items]
        if len(set(indexes)) != len(indexes):
            raise AppError(422, "VALIDATION_FAILED", "同一处冲突在一次请求中只能出现一次。")
        rhash = request_hash(body.model_dump(exclude={"clientSaveKey"}))
        ensure_committed(connection, did)
        carried = _carry_plan(connection, conflict["id"], body)
        for attempt in range(OPTIMISTIC_ATTEMPTS + 1):
            guard = document_lock(did) if attempt == OPTIMISTIC_ATTEMPTS else nullcontext()
            with guard:
                if connection.in_transaction:
                    connection.commit()
                state = {int(item["item_index"]): item for item in connection.execute(
                    "SELECT * FROM document_conflict_items WHERE conflict_id=%s", (conflict["id"],)).fetchall()}
                if any(index not in state for index in indexes):
                    raise AppError(422, "VALIDATION_FAILED", "冲突条目不存在。")
                handled = [state[index] for index in indexes if state[index]["status"] != "open"]
                if handled:
                    if all(item["resolution_key"] == body.clientSaveKey for item in handled) and len(handled) == len(indexes):
                        requested = {item.itemIndex: item.action for item in body.items}
                        if any(item["action"] != requested[int(item["item_index"])] for item in handled):
                            raise AppError(422, "IDEMPOTENCY_KEY_REUSED", "处理键已用于另一份请求，请重新操作。")
                        stored = connection.execute(
                            "SELECT request_hash, result_json FROM document_changes WHERE document_id=%s AND actor_id=%s "
                            "AND client_save_key=%s", (did, principal.user_id, body.clientSaveKey)).fetchone()
                        if stored is not None and stored["request_hash"] != rhash:
                            raise AppError(422, "IDEMPOTENCY_KEY_REUSED", "处理键已用于另一份请求，请重新操作。")
                        return {"data": _resolve_result(connection, did, conflict["id"], principal.user_id, stored,
                                                        indexes), "requestId": request.state.request_id}
                    raise AppError(409, "CONFLICT_ALREADY_RESOLVED", "这处冲突已经处理过。", details={"items": [
                        {"itemIndex": int(item["item_index"]), "status": item["status"], "action": item["action"]}
                        for item in handled]})
                snap = connection.execute("SELECT revision, markdown_content FROM documents WHERE id=%s", (did,)).fetchone()
                revision = int(snap["revision"])
                payload = json.loads(conflict["payload_json"])
                used = _latest_snapshots(connection, conflict["id"])
                for request_item in body.items:
                    carry = carried.get(request_item.itemIndex)
                    seen = carry["seen"] if carry is not None else request_item.mineSnapshotId
                    now_id = (used.get(request_item.itemIndex) or {}).get("id") or ""
                    if seen is not None and seen != now_id:
                        raise _snapshot_changed(conflict["id"], request_item.itemIndex, payload, used.get(request_item.itemIndex))
                # 带上的内容先按“将要写入的快照”参与处理；真正写入在下面的写事务里
                real = dict(used)
                for index, carry in carried.items():
                    used = {**used, index: {"id": "", "markdown": carry["markdown"], "savedAt": None, "tabId": None}}
                try:
                    markdown, results, writes = _apply_actions(connection, did, payload, body.items, used)
                except _Reconflict as again:
                    _refresh_attempt(connection, conflict, payload, again.indexes)
                    error = conflict_error(connection, conflict["id"])
                    error.details["conflicts"] = [item for item in error.details["conflicts"]
                                                  if int(item["itemIndex"]) in again.indexes]
                    raise error from None
                if writes and markdown != snap["markdown_content"]:
                    _check_word(connection, row, markdown)
                if connection.in_transaction:
                    connection.commit()
                try:
                    with write_transaction(connection):
                        current = connection.execute("SELECT revision FROM documents WHERE id=%s", (did,)).fetchone()
                        if int(current["revision"]) != revision:
                            raise _Retry()
                        still_open = connection.execute(
                            "SELECT COUNT(*) AS n FROM document_conflict_items WHERE conflict_id=%s AND status='open' "
                            "AND item_index IN (SELECT value FROM json_each(%s))",
                            (conflict["id"], json.dumps(indexes))).fetchone()["n"]
                        if still_open != len(indexes):
                            raise _Retry()
                        # 审查点 3：处理用的快照必须仍是最新一份（与追加快照在同一把写锁下比较；变了就按新快照重来）
                        fresh = _latest_snapshots(connection, conflict["id"])
                        # 代码审查 M7：带了内容的条目不重来——最新快照不再是客户端看到的那份就 409（事务回滚，什么都不留）
                        for index, carry in carried.items():
                            if ((fresh.get(index) or {}).get("id") or "") != carry["seen"]:
                                raise _snapshot_changed(conflict["id"], index, payload, fresh.get(index))
                        if any((fresh.get(index) or {}).get("id") != (real.get(index) or {}).get("id")
                               for index in indexes if index not in carried):
                            raise _Retry()
                        for index, carry in carried.items():
                            snap_row = append_snapshot(connection, conflict_id=conflict["id"], item_index=index,
                                                       actor=principal.user_id, markdown=carry["markdown"], key=carry["key"],
                                                       source="editor")
                            for result in results:
                                if result["itemIndex"] == index and "mineSnapshotId" in result:
                                    result["mineSnapshotId"], result["mineSavedAt"] = snap_row["id"], snap_row["savedAt"]
                        change_id = None
                        if writes and markdown != snap["markdown_content"]:
                            ensure_document_objects(connection, did)
                            change_id = _save_markdown(connection, did, markdown, revision, kind="conflict_resolution",
                                                       client_save_key=body.clientSaveKey, request_hash=rhash)
                        actions = {item.itemIndex: item.action for item in body.items}
                        for result in results:
                            connection.execute(
                                """UPDATE document_conflict_items SET status=%s, action=%s, resolution_key=%s,
                                   resolved_change_ref=%s, resolved_by=%s, resolved_at=now()
                                   WHERE conflict_id=%s AND item_index=%s AND status='open'""",
                                (result["status"], actions[result["itemIndex"]], body.clientSaveKey, change_id,
                                 principal.user_id, conflict["id"], result["itemIndex"]))
                        status = _rollup(connection, conflict["id"])
                        if status != "open":
                            connection.execute("UPDATE document_conflicts SET resolved_change_ref=COALESCE(%s, resolved_change_ref) "
                                               "WHERE id=%s", (change_id, conflict["id"]))
                        data = _resolve_result(connection, did, conflict["id"], principal.user_id, None, indexes,
                                               change_id=change_id, results=results)
                        if change_id is not None:
                            stored = {key: value for key, value in data.items() if key != "markdownContent"}
                            connection.execute("UPDATE document_changes SET result_json=%s WHERE id=%s",
                                               (json.dumps(stored, ensure_ascii=False), change_id))
                except _Retry:
                    continue
                return {"data": data, "requestId": request.state.request_id}
        raise AppError(503, "DATABASE_BUSY", "文档正在被频繁修改，请稍后重试。", retry_after=1)


MAX_SNAPSHOTS_PER_ITEM = 500
# 审查建议 1（已定）：每条冲突所有快照合计不超过 20MB（按 UTF-8 字节）
MAX_SNAPSHOT_BYTES_PER_CONFLICT = 20 * 1024 * 1024


class MineSnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itemIndex: int = Field(ge=0)
    markdown: str = Field(max_length=2_000_000)
    clientSaveKey: str = Field(min_length=8, max_length=80)
    tabId: str | None = Field(default=None, max_length=80)
    # 审查点 2：同一标签页内单调递增的序号；同标签已存过更大（或相同）序号时，这份是乱序到达的旧快照，不写入
    seq: int | None = Field(default=None, ge=1, le=2**53)


@router.post("/documents/{document_id}/conflicts/{conflict_id}/mine")
def save_mine_snapshot(request: Request, document_id: str, conflict_id: str, body: MineSnapshotBody) -> dict:
    """方案 C 后端第 1 条：冲突后在编辑区继续写的“我的版本”，只追加快照；幂等；只有冲突本人可写。"""
    with connect() as connection:
        principal, did, _row, _manager = _conflict_access(connection, request, document_id)
        conflict = _load_conflict(connection, did, conflict_id)
        if str(conflict["actor_id"]) != str(principal.user_id):
            raise AppError(403, "FORBIDDEN", "只有提交这份修改的人可以保存冲突副本。")
        # 已定：被降为没有编辑权限的作者不能再存快照（与处理冲突一致）
        load_document(connection, did, principal.user_id, is_admin(principal), "edit")
        if connection.in_transaction:
            connection.commit()
        with write_transaction(connection):
            item = connection.execute("SELECT status, action FROM document_conflict_items WHERE conflict_id=%s AND item_index=%s",
                                      (conflict["id"], body.itemIndex)).fetchone()
            if item is None:
                raise AppError(422, "VALIDATION_FAILED", "冲突条目不存在。")
            replayed = connection.execute(
                "SELECT item_index, markdown FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND client_save_key=%s",
                (str(conflict["id"]), body.clientSaveKey)).fetchone()
            if item["status"] != "open" and replayed is None:
                raise AppError(409, "CONFLICT_ALREADY_RESOLVED", "这处冲突已经处理过。", details={"items": [
                    {"itemIndex": body.itemIndex, "status": item["status"], "action": item["action"]}]})
            stale = replayed is None and body.seq is not None and body.tabId is not None and connection.execute(
                "SELECT 1 FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND item_index=%s AND tab_id=%s AND seq>=%s",
                (str(conflict["id"]), body.itemIndex, body.tabId, body.seq)).fetchone() is not None
            if stale:
                snapshot = {**(_latest_snapshots(connection, conflict["id"]).get(body.itemIndex) or {}), "stale": True}
            else:
                hit = _snapshot_limit_hit(connection, conflict["id"], body.itemIndex, len(body.markdown.encode("utf-8"))) \
                    if replayed is None else None
                if hit == "count":
                    raise AppError(422, "SNAPSHOT_LIMIT", "这处冲突的副本已保存太多份，请先在冲突面板处理。",
                                   details={"itemIndex": body.itemIndex})
                if hit == "bytes":
                    used = connection.execute(
                        "SELECT COALESCE(SUM(LENGTH(CAST(markdown AS BLOB))), 0) AS n FROM document_conflict_mine_snapshots WHERE conflict_id=%s",
                        (str(conflict["id"]),)).fetchone()["n"]
                    raise AppError(422, "SNAPSHOT_LIMIT", "这处冲突的副本已超过总大小上限（20MB），请先在冲突面板处理。",
                                   details={"itemIndex": body.itemIndex, "totalBytes": int(used)})
                snapshot = append_snapshot(connection, conflict_id=conflict["id"], item_index=body.itemIndex,
                                           actor=principal.user_id, markdown=body.markdown, key=body.clientSaveKey,
                                           tab_id=body.tabId, seq=body.seq)
        return {"data": {"conflictId": str(conflict["id"]), "itemIndex": body.itemIndex,
                         "mineLatest": {"markdown": snapshot.get("markdown"), "savedAt": snapshot.get("savedAt"),
                                        "tabId": snapshot.get("tabId"), "snapshotId": snapshot.get("id")},
                         "replayed": bool(snapshot.get("replayed")), "unchanged": bool(snapshot.get("unchanged")),
                         "stale": bool(snapshot.get("stale"))},
                "requestId": request.state.request_id}


def _resolve_result(connection, did: UUID, conflict_id: UUID, user_id: UUID, stored, indexes: list[int], *,
                    change_id: UUID | None = None, results: list[dict] | None = None) -> dict:
    document = connection.execute("SELECT revision, markdown_content FROM documents WHERE id=%s", (did,)).fetchone()
    if results is None:
        if stored is not None and stored["result_json"]:
            data = json.loads(stored["result_json"])
            data["markdownContent"] = document["markdown_content"]
            data["replayed"] = True
            return data
        rows = connection.execute(
            "SELECT item_index, status, resolved_change_ref FROM document_conflict_items WHERE conflict_id=%s "
            "AND item_index IN (SELECT value FROM json_each(%s))", (conflict_id, json.dumps(indexes))).fetchall()
        results = [{"itemIndex": int(row["item_index"]), "status": row["status"]} for row in rows]
    status = connection.execute("SELECT status FROM document_conflicts WHERE id=%s", (conflict_id,)).fetchone()["status"]
    mine_open = open_counts(connection, [did], user_id).get(str(did), (0, 0))[0]
    return {"documentRevision": int(document["revision"]), "changeId": str(change_id) if change_id else None,
            "items": results, "conflictStatus": status, "mineOpen": mine_open,
            "markdownContent": document["markdown_content"]}
