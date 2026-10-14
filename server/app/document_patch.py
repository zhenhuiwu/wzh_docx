"""阶段2b 块级补丁保存（01 §4.2，S7）。

`POST /documents/{id}/objects/patch`：前端按对象 ID 提交 update/insert/delete/move。
在**当前存活对象**上逐条检查对象修订号：对象自基线以来未被他人改过才应用；
被改过且内容不同 → MODIFIED，被删 → DELETED，锚点丢失 → ANCHOR_LOST，移动目标失效 → STRUCTURE。
有冲突时整体拒绝，冲突副本按条目存储。atomic=false 时 409 带 appliableOps（无冲突 op 的下标）
和 appliableMarkdown（只应用无冲突 op 的全文），前端用新键再提交（决策2）；atomic=true（AI/结构/恢复类，
必须整体成功或整体失败）时 409 不带这两项，避免前端拆开重发（审查 S7）。
写入沿用整篇路径的乐观重试 + 第4次文档锁（01 §5.4）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect, write_transaction
from app.document_access import document_principal, is_admin, load_document
from app.document_commit import (OPTIMISTIC_ATTEMPTS, _Retry, _changed_by, _names, _person, _theirs_info, document_lock,
                                 replay, request_hash, store_conflict)
from app.document_objects import ensure_committed, live_objects, locations_of
from app.errors import AppError
from app.markdown_blocks import Block, assemble, split
from app.models import format_timestamp

router = APIRouter()


class PatchOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["update", "insert", "delete", "move"]
    objectId: str | None = None
    tempId: str | None = Field(default=None, max_length=64)
    baseRevision: int | None = Field(default=None, ge=0)
    markdown: str | None = Field(default=None, max_length=200000)
    afterObjectId: str | None = None
    beforeObjectId: str | None = None


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientSaveKey: str = Field(min_length=8, max_length=80)
    baseDocumentRevision: int = Field(ge=0)
    atomic: bool = False
    # 复验 14：本页先前发出的保存（Ctrl+S 的整篇保存等）的 clientSaveKey。关页保存以它们之前的修订为基准时，
    # 这些保存本人改过的段落不算冲突，按它们之后的修订重新定基（见 _rebase_own）。
    afterSaveKeys: list[str] = Field(default_factory=list, max_length=20)
    ops: list[PatchOp] = Field(min_length=1, max_length=500)


def _validate(ops: list[PatchOp]) -> None:
    seen: set[str] = set()
    temps: set[str] = set()
    for index, op in enumerate(ops):
        where = f"第 {index + 1} 项修改"
        if op.op in {"update", "delete", "move"}:
            if not op.objectId:
                raise AppError(422, "VALIDATION_FAILED", f"{where}缺少对象 ID。")
            if op.objectId in seen:
                raise AppError(422, "VALIDATION_FAILED", "同一对象在一次保存中只能出现一次。")
            seen.add(op.objectId)
        if op.op in {"update", "delete"} and op.baseRevision is None:
            raise AppError(422, "VALIDATION_FAILED", f"{where}缺少对象修订号。")
        if op.op in {"update", "insert"} and not (op.markdown or "").strip():
            raise AppError(422, "VALIDATION_FAILED", f"{where}内容为空；删除段落请使用 delete。")
        if op.op == "insert":
            if not op.tempId:
                raise AppError(422, "VALIDATION_FAILED", f"{where}缺少临时 ID。")
            if op.tempId in temps:
                raise AppError(422, "VALIDATION_FAILED", "临时 ID 重复。")
            temps.add(op.tempId)


class _Doc:
    """当前存活对象的可变序列：每项 (objectId|None, Block, opIndex|None)。"""

    def __init__(self, live):
        self.items: list[list] = [[str(row["id"]), Block(row["kind"], row["body"], row["gap_after"], row["heading_level"]), None]
                                  for row in live]
        self.temps: dict[str, int] = {}

    def index(self, object_id: str | None) -> int | None:
        for position, item in enumerate(self.items):
            if item[0] == object_id:
                return position
        return None

    def blocks_of(self, markdown: str, gap: str) -> list[Block]:
        _, blocks = split(markdown.strip("\n"))
        if not blocks:
            return []
        last = blocks[-1]
        return blocks[:-1] + [Block(last.kind, last.body, gap, last.heading_level)]

    def replace(self, position: int, markdown: str, op_index: int) -> None:
        gap = self.items[position][1].gap_after or "\n\n"
        blocks = self.blocks_of(markdown, gap)
        first = [self.items[position][0], blocks[0], op_index]
        rest = [[None, block, op_index] for block in blocks[1:]]
        self.items[position:position + 1] = [first, *rest]

    def insert(self, position: int, markdown: str, op_index: int) -> int:
        blocks = self.blocks_of(markdown, "\n\n")
        if position > 0 and not self.items[position - 1][1].gap_after:
            prev = self.items[position - 1][1]
            self.items[position - 1][1] = Block(prev.kind, prev.body, "\n\n", prev.heading_level)
        self.items[position:position] = [[None, block, op_index] for block in blocks]
        return position + len(blocks)

    def markdown(self, prefix: str) -> str:
        blocks = [item[1] for item in self.items]
        # 末段移到中间时需要分隔，否则后续标题会粘进表格并导致对象身份失效。
        for index, block in enumerate(blocks[:-1]):
            if not block.gap_after:
                blocks[index] = Block(block.kind, block.body, "\n\n", block.heading_level)
        return assemble(prefix, blocks)


def _object(connection, document_id: UUID, object_id: str):
    # 删除人取该对象 delete 修改记录的提交人（与 GET /objects 一致）
    return connection.execute(
        """SELECT o.id, o.kind, o.body, o.revision, o.deleted_at, o.deleted_doc_revision, o.updated_at,
                  COALESCE(c.actor_id, o.updated_by) AS updated_by
           FROM document_objects o
           LEFT JOIN document_object_changes oc ON oc.object_id = o.id AND oc.op = 'delete'
           LEFT JOIN document_changes c ON c.id = oc.change_id
           WHERE o.document_id=%s AND o.id=%s""", (document_id, object_id)).fetchone()


def _base_body(connection, object_id: str, revision: int | None, fallback: str) -> str:
    if revision is None:
        return fallback
    row = connection.execute(
        "SELECT after_body FROM document_object_changes WHERE object_id=%s AND after_revision=%s ORDER BY rowid DESC LIMIT 1",
        (object_id, revision)).fetchone()
    return row["after_body"] if row is not None and row["after_body"] is not None else fallback


def _rebase_own(connection, did: UUID, user_id, keys: list[str], base_rev: int, ops: list[PatchOp]):
    """同一页面先后发出的保存不能自冲突：对象在 op.baseRevision 之后的每次修改若都出自本人、且属于 afterSaveKeys
    里的保存，就把 op 的基准挪到最新修订（相当于先应用了自己的那次保存再改）；只要夹着别人的修改就保持原样，照常判冲突。"""
    if not keys:
        return base_rev, ops
    own = connection.execute(
        "SELECT doc_revision, actor_id, client_save_key FROM document_changes WHERE document_id=%s AND doc_revision>%s ORDER BY doc_revision",
        (did, base_rev)).fetchall()
    doc_base = base_rev
    for row in own:  # 文档级基准：连续的本人保存才能跨过（只影响“别人改过”的提示）
        if str(row["actor_id"]) != str(user_id) or row["client_save_key"] not in keys:
            break
        doc_base = int(row["doc_revision"])
    out = []
    for op in ops:
        if op.op in {"update", "delete", "move"} and op.objectId and op.baseRevision is not None:
            rows = connection.execute(
                """SELECT oc.after_revision, oc.op, c.actor_id, c.client_save_key FROM document_object_changes oc
                   JOIN document_changes c ON c.id = oc.change_id
                   WHERE oc.object_id=%s AND c.document_id=%s AND (oc.after_revision IS NULL OR oc.after_revision>%s)
                   ORDER BY c.doc_revision""", (op.objectId, did, op.baseRevision)).fetchall()
            if rows and all(str(r["actor_id"]) == str(user_id) and r["client_save_key"] in keys
                            and r["op"] != "delete" and r["after_revision"] is not None for r in rows):
                op = op.model_copy(update={"baseRevision": int(rows[-1]["after_revision"])})
        out.append(op)
    return doc_base, out


def _apply(connection, document_id: UUID, live, ops: list[PatchOp], skip: set[int] | None = None):
    """在当前存活对象上应用 ops；返回 (_Doc, 冲突列表[(opIndex, reason, info)], 每个 op 的结果)。"""
    doc = _Doc(live)
    by_id = {str(row["id"]): row for row in live}
    conflicts: list[tuple[int, str, dict]] = []
    applied: list[dict] = []
    for index, op in enumerate(ops):
        if skip is not None and index in skip:
            continue
        row = by_id.get(op.objectId or "")
        if op.op in {"update", "delete", "move"} and row is None:
            gone = _object(connection, document_id, op.objectId)
            if gone is None:
                raise AppError(422, "VALIDATION_FAILED", "要修改的段落不属于这份文档，请刷新后重试。")
            if op.op == "delete":
                applied.append({"index": index, "objectId": op.objectId, "deleted": True})
                continue
            conflicts.append((index, "DELETED" if op.op == "update" else "STRUCTURE", {"gone": gone}))
            continue
        if op.op == "update":
            if int(row["revision"]) != op.baseRevision and row["body"] != (op.markdown or "").strip("\n"):
                conflicts.append((index, "MODIFIED", {"row": row}))
                continue
            doc.replace(doc.index(op.objectId), op.markdown or "", index)
            applied.append({"index": index, "objectId": op.objectId})
        elif op.op == "delete":
            if int(row["revision"]) != op.baseRevision:
                conflicts.append((index, "DELETED", {"row": row, "mineDeleted": True}))
                continue
            del doc.items[doc.index(op.objectId)]
            applied.append({"index": index, "objectId": op.objectId, "deleted": True})
        elif op.op == "insert":
            anchor_after = doc.temps.get(op.afterObjectId) if op.afterObjectId in doc.temps else doc.index(op.afterObjectId)
            anchor_before = doc.index(op.beforeObjectId)
            if op.afterObjectId and anchor_after is not None:
                position = anchor_after + 1
                while position < len(doc.items) and doc.items[position][0] is None and doc.items[position][2] is not None \
                        and ops[doc.items[position][2]].op == "insert" and ops[doc.items[position][2]].afterObjectId == op.afterObjectId:
                    position += 1
            elif op.beforeObjectId and anchor_before is not None:
                position = anchor_before
            elif not op.afterObjectId and not op.beforeObjectId:
                position = 0
            else:
                conflicts.append((index, "ANCHOR_LOST", {}))
                continue
            end = doc.insert(position, op.markdown or "", index)
            doc.temps[op.tempId] = end - 1
            applied.append({"index": index, "tempId": op.tempId})
        else:  # move
            target = doc.index(op.afterObjectId) if op.afterObjectId else -1
            if op.afterObjectId and target is None:
                conflicts.append((index, "STRUCTURE", {"row": row}))
                continue
            source = doc.index(op.objectId)
            item = doc.items.pop(source)
            target = doc.index(op.afterObjectId) if op.afterObjectId else -1
            doc.items.insert(target + 1, item)
            applied.append({"index": index, "objectId": op.objectId})
    return doc, conflicts, applied


def _items(connection, document_id: UUID, live, ops: list[PatchOp], conflicts) -> list[dict]:
    people = [row["updated_by"] for row in live] + [info["gone"]["updated_by"] for _, _, info in conflicts if info.get("gone")]
    names = _names(connection, people)
    locations = locations_of([(row["kind"], row["body"], row["heading_level"]) for row in live])
    ids = [str(row["id"]) for row in live]
    items = []
    for item_index, (op_index, reason, info) in enumerate(conflicts):
        op = ops[op_index]
        row, gone = info.get("row"), info.get("gone")
        position = ids.index(str(row["id"])) if row is not None else None
        mine = {"deleted": True, "markdown": ""} if op.op == "delete" else {"markdown": (op.markdown or "").strip("\n")}
        if op.op == "move":
            mine = {"markdown": (row or gone)["body"]}
        item = {"itemIndex": item_index, "attempt": 1, "reason": reason, "opIndexes": [op_index],
                "objectIds": [op.objectId] if op.objectId else [],
                "location": locations[position] if position is not None else {"headingPath": [], "ordinal": None, "kind": "other", "label": None},
                "theirsRevisions": {str(row["id"]): int(row["revision"])} if row is not None else {},
                "afterObjectId": (ids[position - 1] if position else None) if position is not None else (op.afterObjectId or None)}
        if row is not None:
            item["theirs"] = _theirs_info(connection, document_id, [row], names)
            item["base"] = {"markdown": _base_body(connection, str(row["id"]), op.baseRevision, row["body"])}
        elif gone is not None:
            item["theirs"] = {"markdown": gone["body"], "deleted": True, "revision": int(gone["revision"]),
                              "deletedBy": _person(gone["updated_by"], names.get(str(gone["updated_by"]))),
                              "deletedAt": format_timestamp(gone["deleted_at"]) if gone["deleted_at"] else None}
            item["base"] = {"markdown": _base_body(connection, str(gone["id"]), op.baseRevision, gone["body"])}
        else:  # ANCHOR_LOST：锚点被删，新段落没有位置
            item["theirs"] = {"markdown": "", "deleted": True, "revision": None}
            item["base"] = {"markdown": ""}
        item["mine"] = mine
        items.append(item)
    return items


def _reject_word_headings(connection, did: UUID, principal, ops) -> None:
    """验收 40 P2-4：Word 里套正文样式的标题段落（如“1.1 标识”）和真标题一样固定，不能按块修改、删除或移动。"""
    targets = {op.objectId for op in ops if op.op in ("update", "delete", "move") and op.objectId}
    if not targets:
        return
    # 复验 P2-1：原先按整篇编辑稿（要等 Word 往返预览、并给全部对象算纯文本）判断，大文档一次按块保存要 5s 以上，
    # 关页时的 keepalive 保存因此常常晚于重新打开。改用 43 的原稿标题表（按存储键缓存，与预览的 data-word-heading 同一判断），只看本次涉及的对象。
    from app.document_objects import live_objects
    from app.fast_preview import word_heading_levels
    load_document(connection, did, principal.user_id, is_admin(principal), "edit")
    wanted = {str(item) for item in targets}
    rows = [item for item in live_objects(connection, did) if str(item["id"]) in wanted]
    locked = set(word_heading_levels(connection, did, rows))
    if connection.in_transaction:
        connection.commit()
    if locked:
        raise AppError(403, "HEADING_LOCKED", "标题段落已固定，不能在这里修改；需要改标题请联系文档负责人。",
                       details={"objectIds": sorted(locked)})


@router.post("/documents/{document_id}/objects/patch")
def patch_objects(request: Request, document_id: str, body: PatchBody) -> dict:
    from app.documents import _check_word, _save_markdown

    principal = document_principal(request)
    try:
        did = UUID(document_id)
    except ValueError as exc:
        raise AppError(404, "NOT_FOUND", "文档不存在或你无权访问。") from exc
    _validate(body.ops)
    rhash = request_hash({"patch": body.model_dump(exclude={"clientSaveKey"})})
    from app.patch_inflight import saving
    with saving(did), connect() as connection:
        load_document(connection, did, principal.user_id, is_admin(principal), "edit")
        replayed = replay(connection, did, principal.user_id, body.clientSaveKey, rhash)
        if replayed is not None:
            return {"data": _public(replayed), "requestId": request.state.request_id}
        ensure_committed(connection, did)
        from app.patch_inflight import wait_whole_saves
        wait_whole_saves(did)
        _reject_word_headings(connection, did, principal, body.ops)
        for attempt in range(OPTIMISTIC_ATTEMPTS + 1):
            guard = document_lock(did) if attempt == OPTIMISTIC_ATTEMPTS else nullcontext()
            with guard:
                if connection.in_transaction:
                    connection.commit()
                snap = connection.execute("SELECT revision, objects_prefix, markdown_content FROM documents WHERE id=%s",
                                          (did,)).fetchone()
                revision, prefix, theirs = int(snap["revision"]), snap["objects_prefix"] or "", snap["markdown_content"] or ""
                if body.baseDocumentRevision > revision:
                    raise AppError(422, "VALIDATION_FAILED", "保存所依据的版本不存在。")
                base_rev, ops = _rebase_own(connection, did, principal.user_id, body.afterSaveKeys,
                                            body.baseDocumentRevision, body.ops)
                live = list(live_objects(connection, did))
                doc, conflicts, applied = _apply(connection, did, live, ops)
                if conflicts:
                    conflicted = {op_index for op_index, _, _ in conflicts}
                    partial = None if body.atomic else _apply(connection, did, live, ops, skip=conflicted)[0]
                    # 冲突副本里的“我的全文”：在当前正文上按我的意图应用全部 op（冲突处用我的版本）。
                    mine_doc = _Doc(live)
                    for op_index, op in enumerate(ops):
                        if op.op == "update" and mine_doc.index(op.objectId) is not None:
                            mine_doc.replace(mine_doc.index(op.objectId), op.markdown or "", op_index)
                    items = _items(connection, did, live, ops, conflicts)
                    error = store_conflict(connection, document_id=did, actor=principal.user_id,
                                           base_revision=base_rev, doc_revision=revision, source="patch",
                                           rhash=rhash, mine_md=mine_doc.markdown(prefix), items=items,
                                           appliable_md=None if partial is None else partial.markdown(prefix))
                    if body.atomic:
                        for key in ("appliableOps", "appliableMarkdown", "appliableBaseRevision"):
                            error.details.pop(key, None)
                    else:
                        error.details["appliableOps"] = [index for index in range(len(ops)) if index not in conflicted]
                    raise error
                merged_md = doc.markdown(prefix)
                merged_flag = revision != base_rev
                if merged_md == theirs:
                    return {"data": {"documentRevision": revision, "merged": merged_flag, "changeId": None, "applied": applied,
                                     "othersChangedSince": base_rev if merged_flag else None,
                                     "changedBy": _changed_by(connection, did, base_rev, principal.user_id),
                                     "markdownContent": theirs},
                            "requestId": request.state.request_id}
                current_row = load_document(connection, did, principal.user_id, is_admin(principal), "edit")
                _check_word(connection, current_row, merged_md)
                if connection.in_transaction:
                    connection.commit()
                try:
                    with write_transaction(connection):
                        now = connection.execute("SELECT revision FROM documents WHERE id=%s", (did,)).fetchone()
                        if int(now["revision"]) != revision:
                            raise _Retry()
                        moved = {op.objectId for op in ops if op.op == "move" and op.objectId}
                        change_id = _save_markdown(connection, did, merged_md, revision, kind="edit", merged=merged_flag,
                                                   client_save_key=body.clientSaveKey, request_hash=rhash,
                                                   identities=[item[0] for item in doc.items], moved=moved)
                        after = list(live_objects(connection, did))
                        _resolve_ids(doc, after, applied)
                        data = {"documentRevision": revision + 1, "revision": revision + 1, "merged": merged_flag,
                                "changeId": str(change_id), "applied": applied,
                                "othersChangedSince": base_rev if merged_flag else None,
                                "changedBy": _changed_by(connection, did, base_rev, principal.user_id)}
                        connection.execute("UPDATE document_changes SET result_json=%s WHERE id=%s",
                                           (json.dumps(data, ensure_ascii=False), change_id))
                except _Retry:
                    continue
                except AppError as error:
                    if error.code == "SECTION_NOT_ASSIGNED":  # A07：被拒的提交在独立短事务里保留（字不丢）
                        from app.document_assignments import store_rejected
                        store_rejected(connection, did, principal.user_id, base_rev, merged_md, None, error)
                    raise
                except sqlite3.IntegrityError:
                    again = replay(connection, did, principal.user_id, body.clientSaveKey, rhash)
                    if again is not None:
                        return {"data": _public(again), "requestId": request.state.request_id}
                    raise
                data["markdownContent"] = merged_md
                from app.fast_preview import warm_after_commit
                warm_after_commit(did, revision + 1)
                return {"data": data, "requestId": request.state.request_id}
    raise AppError(503, "DATABASE_BUSY", "文档正在被频繁修改，请稍后重试。", retry_after=1)


def _resolve_ids(doc: _Doc, after, applied: list[dict]) -> None:
    """写入后按位置把 tempId / 对象 ID 对应到存活对象，返回新 ID 与修订号。"""
    if len(after) != len(doc.items):
        return
    by_op: dict[int, int] = {}
    for position, item in enumerate(doc.items):
        if item[2] is not None and item[2] not in by_op:
            by_op[item[2]] = position
    for entry in applied:
        if entry.get("deleted"):
            continue
        position = by_op.get(entry["index"])
        if position is None:
            position = next((p for p, item in enumerate(doc.items) if item[0] == entry.get("objectId")), None)
        if position is None:
            continue
        entry["objectId"] = str(after[position]["id"])
        entry["revision"] = int(after[position]["revision"])


def _public(data: dict) -> dict:
    data = dict(data)
    data.setdefault("documentRevision", data.get("revision"))
    return data
