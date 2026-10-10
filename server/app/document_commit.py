"""合并提交、冲突副本与“处理并提交”（01-阶段2设计 §4.3、§4.4、§5.3、§5.4）。

提交流程（5.4）：幂等重放 → 最多 3 轮“锁外合并 + Word 预检，BEGIN IMMEDIATE 内 CAS 写入” → 第 4 轮在进程内文档锁里重做。
冲突副本在独立短事务里先落库，再返回 409；payload_json 中“我的内容”永不修改、永不删除（决策5）。
"""

from __future__ import annotations

import hashlib
import json
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


def _theirs_info(connection, document_id: UUID, rows: list, names: dict) -> dict:
    first = rows[0]
    return {"markdown": "\n\n".join(row["body"] for row in rows), "revision": int(first["revision"]), "deleted": False,
            "updatedBy": _person(first["updated_by"], names.get(str(first["updated_by"]))),
            "updatedAt": format_timestamp(first["updated_at"])}


def build_items(connection, document_id: UUID, base_md: str, theirs_md: str, result: MergeResult,
                base_revision: int) -> list[dict]:
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
            item["theirs"] = _theirs_info(connection, document_id, rows, names)
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


def _items_view(connection, conflict_id: UUID, payload: dict, only_open: bool = False) -> list[dict]:
    rows = {int(row["item_index"]): row for row in connection.execute(
        "SELECT item_index, attempt, status, action FROM document_conflict_items WHERE conflict_id=%s", (conflict_id,)).fetchall()}
    result = []
    for item in payload.get("conflicts", []):
        row = rows.get(int(item["itemIndex"]))
        if only_open and row is not None and row["status"] != "open":
            continue
        view = _public_item(item, row)
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
                   rhash: str, mine_md: str, items: list[dict], appliable_md: str | None) -> AppError:
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
    return conflict_error(connection, conflict_id)


# ---------------------------------------------------------------- 整篇合并提交

def commit_whole(connection, *, document_id: UUID, actor: UUID, base_revision: int, mine_md: str, kind: str,
                 client_save_key: str | None, check_word: Callable[[str], None], source: str = "content",
                 extra: dict | None = None, after_write: Callable | None = None,
                 template_update: Callable[[dict], dict] | None = None) -> dict:
    """allowMerge 的整篇提交；返回 {revision, merged, changeId, changedBy, markdownContent?, noop?}。"""
    from app.documents import _save_markdown

    rhash = request_hash({"base": base_revision, "markdown": mine_md, "kind": kind, **(extra or {})})
    replayed = replay(connection, document_id, actor, client_save_key, rhash)
    if replayed is not None:
        return replayed
    base_md = _version_markdown(connection, document_id, base_revision)
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
            merged_flag = revision != base_revision
            if merged_flag:
                ensure_committed(connection, document_id)
                others = connection.execute(
                    """SELECT COUNT(*) AS n FROM document_changes WHERE document_id=%s AND doc_revision > %s
                       AND kind NOT IN ('backfill','realign') AND (actor_id IS NULL OR actor_id <> %s)""",
                    (document_id, base_revision, actor)).fetchone()["n"]
                result = merge3(base_md, theirs, mine_md, dedupe_inserts=others == 0)
                if result.conflicts or result.prefix_conflict:
                    if not result.conflicts:
                        result.conflicts = []
                    items = build_items(connection, document_id, base_md, theirs, result, base_revision)
                    if not items:
                        items = [_whole_item(0, "STRUCTURE", mine_md, theirs, base_md)]
                    raise store_conflict(connection, document_id=document_id, actor=actor, base_revision=base_revision,
                                         doc_revision=revision, source=source, rhash=rhash, mine_md=mine_md,
                                         items=items, appliable_md=result.markdown)
                merged = result.markdown
            else:
                merged = mine_md
            if merged == theirs:
                return {"revision": revision, "merged": merged_flag, "changeId": None, "noop": True,
                        "changedBy": _changed_by(connection, document_id, base_revision, actor), "markdownContent": theirs}
            try:
                check_word(merged)
            except AppError as error:
                if error.code not in {"AI_FORMAT_INVALID", "WORD_BLOCK_PROTECTED"} or not merged_flag:
                    raise
                item = _whole_item(0, "FORMAT", mine_md, theirs, base_md)
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


def _whole_item(index: int, reason: str, mine_md: str, theirs_md: str, base_md: str) -> dict:
    return {"itemIndex": index, "attempt": 1, "reason": reason, "opIndexes": [], "objectIds": [],
            "location": {"headingPath": [], "ordinal": None, "kind": "other", "label": None},
            "base": {"markdown": base_md}, "theirs": {"markdown": theirs_md, "deleted": False, "revision": None},
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


def _item_row(connection, conflict, item: dict, row) -> dict:
    value = _public_item(item, row)
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
        items = []
        for row in rows:
            payload = payloads.setdefault(str(row["id"]), json.loads(row["payload_json"]))
            item = next(entry for entry in payload["conflicts"] if int(entry["itemIndex"]) == int(row["item_index"]))
            items.append(_item_row(connection, row, item, row))
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


def _apply_actions(connection, did: UUID, payload: dict, request_items: list[ResolveItem]) -> tuple[str, list[dict], bool]:
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

    def nearest_live(object_id: str | None) -> int:
        """删除前位置之前最近的存活对象之后（返回插入下标）。"""
        if object_id is None:
            return 0
        row = connection.execute("SELECT position FROM document_objects WHERE id=%s", (object_id,)).fetchone()
        if row is None:
            return 0
        before = [index for index, live_row in enumerate(live) if live_row["position"] < row["position"]]
        target = before[-1] if before else -1
        current = position_of(str(live[target]["id"])) if target >= 0 else -1
        return (current if current is not None else -1) + 1

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
            insert_at(index + 1 if index is not None else nearest_live(ref), mine)
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
                target = nearest_live(ref)
        else:
            target = nearest_live(ref)
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
        theirs = _theirs_info(connection, conflict["document_id"], rows, names) if rows else {
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
                try:
                    markdown, results, writes = _apply_actions(connection, did, payload, body.items)
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
