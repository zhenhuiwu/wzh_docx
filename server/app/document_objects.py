"""内容对象：惰性回填、提交时同步、修改记录与对象读取（01-阶段2设计 §3.3、§3.4、§4.1、§4.5、§4.6）。

不变量：I1 assemble(objects_prefix, 存活对象按 position) == documents.markdown_content；
        I2 objects_revision == revision（对象模型可用时）。
documents.markdown_content 仍是唯一的“物化全文”，旧代码可直接读写；对象落后时由 ensure_document_objects 重新对齐。
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
from contextvars import ContextVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request

from app.db import MAINTENANCE_BUSY_MS, connect
from app.document_access import document_principal, is_admin, load_document, permissions_of
from app.document_location import LocItem, compute_locations
from app.errors import AppError
from app.markdown_blocks import Block, assemble, canonical_key, heading_text, split
from app.models import format_timestamp

logger = logging.getLogger(__name__)
router = APIRouter()

POSITION_STEP = 1024
# 当前请求的操作人：document_principal 设置；每个请求在线程池中有独立的上下文副本，不会串到别的请求。
ACTOR: ContextVar[UUID | None] = ContextVar("document_actor", default=None)


def set_actor(user_id: UUID | None) -> None:
    ACTOR.set(user_id)


def current_actor() -> UUID | None:
    return ACTOR.get()


# ---------------------------------------------------------------- 位置

_INLINE_MARK = re.compile(r"[*_`]|!\[[^\]]*\]\([^)]*\)")


def _loc_item(kind: str, body: str, level: int | None) -> LocItem:
    if kind == "heading":
        return LocItem("heading", heading_text(body), level)
    return LocItem(kind, " ".join(_INLINE_MARK.sub("", body).split()))


def locations_of(items: list[tuple[str, str, int | None]]) -> list[dict[str, object]]:
    return compute_locations([_loc_item(*item) for item in items])


# ---------------------------------------------------------------- 读取

def live_objects(connection, document_id: UUID) -> list:
    return connection.execute(
        """SELECT id, kind, heading_level, position, body, gap_after, revision, updated_doc_revision,
                  updated_by, updated_at FROM document_objects
           WHERE document_id=%s AND deleted_at IS NULL ORDER BY position, created_at""",
        (document_id,),
    ).fetchall()


def _blocks_of(rows) -> list[Block]:
    return [Block(row["kind"], row["body"], row["gap_after"], row["heading_level"]) for row in rows]


def check_invariants(connection, document_id: UUID) -> bool:
    """I1 + I2；供测试与 --check 使用。"""
    row = connection.execute(
        "SELECT revision, markdown_content, objects_revision, objects_prefix FROM documents WHERE id=%s", (document_id,),
    ).fetchone()
    if row is None or row["objects_revision"] is None:
        return False
    text = assemble(row["objects_prefix"], _blocks_of(live_objects(connection, document_id)))
    return text == (row["markdown_content"] or "") and row["objects_revision"] == row["revision"]


# ---------------------------------------------------------------- 写入

def _insert_change(connection, document_id: UUID, *, doc_revision: int, base_revision: int, actor: UUID | None,
                   kind: str, merged: bool = False, ai_assisted: bool = False, client_save_key: str | None = None,
                   request_hash: str | None = None) -> UUID:
    change_id = uuid4()
    connection.execute(
        """INSERT INTO document_changes (id, document_id, doc_revision, base_revision, actor_id, kind, ai_assisted,
                                         merged, client_save_key, request_hash, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())""",
        (change_id, document_id, doc_revision, base_revision, actor, kind, int(ai_assisted), int(merged),
         client_save_key, request_hash),
    )
    return change_id


def _sync(connection, document_id: UUID, blocks: list[Block], doc_revision: int, actor: UUID | None,
          change_id: UUID | None, identities: list[str | None] | None = None, moved: set[str] | None = None) -> int:
    """把存活对象对齐到 blocks；返回“处”数（插入/实质修改/删除的对象数）。

    规范键相同的块保留 ID 与 revision（只同步字节）；替换区间内 kind 相同的 1:1 保留 ID 并 revision+1；其余增删。
    """
    # 写锁内：只取对齐需要的列，不做时间/UUID 解析（大文档逐行转换开销明显）
    cursor = connection._conn.cursor()
    cursor.row_factory = None
    live = [dict(zip(("id", "kind", "heading_level", "position", "body", "gap_after", "revision"), values))
            for values in cursor.execute(
                "SELECT id, kind, heading_level, position, body, gap_after, revision FROM document_objects "
                "WHERE document_id=? AND deleted_at IS NULL ORDER BY position, created_at", (str(document_id),)).fetchall()]
    old = [(row["kind"], row["body"]) for row in live]
    new = [(block.kind, block.body) for block in blocks]
    order: list[UUID] = []
    records: list[tuple[str, UUID, int | None, int | None, str | None, str | None, int, int | None]] = []
    # (op, object_id, before_rev, after_rev, before_body, after_body, new_index|old_index, is_delete)

    def keep(row, block: Block, index: int) -> None:
        if row["body"] != block.body or row["gap_after"] != block.gap_after or row["kind"] != block.kind \
                or row["heading_level"] != block.heading_level:
            substantive = row["body"] != block.body and canonical_key(row["body"]) != canonical_key(block.body)
            if substantive:
                connection.execute(
                    """UPDATE document_objects SET body=%s, gap_after=%s, kind=%s, heading_level=%s, revision=revision+1,
                       updated_doc_revision=%s, updated_by=%s, updated_at=now() WHERE id=%s""",
                    (block.body, block.gap_after, block.kind, block.heading_level, doc_revision, actor, row["id"]),
                )
                records.append(("update", row["id"], row["revision"], row["revision"] + 1, row["body"], block.body, index, None))
            else:
                connection.execute(
                    "UPDATE document_objects SET body=%s, gap_after=%s, kind=%s, heading_level=%s WHERE id=%s",
                    (block.body, block.gap_after, block.kind, block.heading_level, row["id"]),
                )
        order.append(row["id"])

    def insert(block: Block, index: int) -> None:
        object_id = uuid4()
        connection.execute(
            """INSERT INTO document_objects (id, document_id, kind, heading_level, position, body, gap_after, revision,
                   created_doc_revision, updated_doc_revision, created_by, updated_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,now(),now())""",
            (object_id, document_id, block.kind, block.heading_level, (index + 1) * POSITION_STEP, block.body,
             block.gap_after, doc_revision, doc_revision, actor, actor),
        )
        records.append(("insert", object_id, None, 0, None, block.body, index, None))
        order.append(object_id)

    deleted_rows: list[tuple[object, int]] = []

    def delete(row, old_index: int) -> None:
        connection.execute(
            "UPDATE document_objects SET deleted_at=now(), deleted_doc_revision=%s WHERE id=%s", (doc_revision, row["id"]),
        )
        deleted_rows.append((row["id"], old_index))
        records.append(("delete", row["id"], row["revision"], None, row["body"], None, old_index, 1))

    by_id = {str(row["id"]): (index, row) for index, row in enumerate(live)}
    if identities is not None and len(identities) == len(blocks) and \
            all(value is None or value in by_id for value in identities) and \
            len({value for value in identities if value}) == len([value for value in identities if value]):
        # 2b 补丁（S7）：前端带对象 ID，按 ID 对应，不靠 diff 猜测（复制相同文字、移动后 ID 仍指向原段）。
        kept = {value for value in identities if value}
        for old_index, row in enumerate(live):
            if str(row["id"]) not in kept:
                delete(row, old_index)
        for index, (value, block) in enumerate(zip(identities, blocks)):
            if value is None:
                insert(block, index)
                continue
            row = by_id[value][1]
            before = len(records)
            keep(row, block, index)
            if moved and value in moved and len(records) == before:
                records.append(("move", row["id"], row["revision"], row["revision"], row["body"], row["body"], index, None))
        old = new  # 跳过下面的 diff 对齐
        matcher_ops: list = []
    else:
        matcher_ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in matcher_ops:
        if tag == "equal":
            for offset in range(i2 - i1):
                keep(live[i1 + offset], blocks[j1 + offset], j1 + offset)
            continue
        inner = difflib.SequenceMatcher(None, [row["kind"] for row in live[i1:i2]],
                                        [block.kind for block in blocks[j1:j2]], autojunk=False)
        for itag, a1, a2, b1, b2 in inner.get_opcodes():
            if itag == "equal":
                for offset in range(a2 - a1):
                    keep(live[i1 + a1 + offset], blocks[j1 + b1 + offset], j1 + b1 + offset)
                continue
            for offset in range(a1, a2):
                delete(live[i1 + offset], i1 + offset)
            for offset in range(b1, b2):
                insert(blocks[j1 + offset], j1 + offset)

    # 复审 M3：记下删除时紧挨在前面、本次仍存活的对象（'^' = 文档开头），恢复时按它定位
    survivors = {str(value) for value in order}
    for object_id, old_index in deleted_rows:
        before = next((str(live[index]["id"]) for index in range(old_index - 1, -1, -1)
                       if str(live[index]["id"]) in survivors), "^")
        connection.execute("UPDATE document_objects SET deleted_after_ref=%s WHERE id=%s", (before, object_id))
    positions = {row["id"]: row["position"] for row in live}
    for index, object_id in enumerate(order):
        wanted = (index + 1) * POSITION_STEP
        if positions.get(object_id, wanted) != wanted:
            connection.execute("UPDATE document_objects SET position=%s WHERE id=%s", (wanted, object_id))

    if change_id is not None and records:
        after_locations = locations_of([(b.kind, b.body, b.heading_level) for b in blocks])
        before_locations = locations_of([(row["kind"], row["body"], row["heading_level"]) for row in live]) \
            if any(record[7] for record in records) else []
        for op, object_id, before_rev, after_rev, before_body, after_body, index, is_delete in records:
            location = before_locations[index] if is_delete else after_locations[index]
            connection.execute(
                """INSERT INTO document_object_changes (change_id, object_id, op, before_revision, after_revision,
                       before_body, after_body, location_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (change_id, object_id, op, before_rev, after_rev, before_body, after_body,
                 json.dumps(location, ensure_ascii=False)),
            )
    return len(records)


def ensure_document_objects(connection, document_id: UUID) -> bool:
    """惰性回填 / 重新对齐（3.4）；返回对象模型是否可用。调用方负责事务与提交。"""
    row = connection.execute(
        "SELECT revision, markdown_content, objects_revision FROM documents WHERE id=%s", (document_id,),
    ).fetchone()
    if row is None:
        return False
    revision, text = int(row["revision"]), row["markdown_content"] or ""
    if row["objects_revision"] is not None and int(row["objects_revision"]) >= revision:
        return True
    prefix, blocks = split(text)
    if assemble(prefix, blocks) != text:
        logger.warning("document %s cannot round-trip through the block splitter; staying in whole-document mode", document_id)
        return False
    if row["objects_revision"] is None:
        connection.execute("UPDATE document_objects SET deleted_at=now(), deleted_doc_revision=%s "
                           "WHERE document_id=%s AND deleted_at IS NULL", (revision, document_id))
        _insert_change(connection, document_id, doc_revision=revision, base_revision=revision, actor=None, kind="backfill")
        for index, block in enumerate(blocks):
            connection.execute(
                """INSERT INTO document_objects (id, document_id, kind, heading_level, position, body, gap_after, revision,
                       created_doc_revision, updated_doc_revision, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,now(),now())""",
                (uuid4(), document_id, block.kind, block.heading_level, (index + 1) * POSITION_STEP, block.body,
                 block.gap_after, revision, revision),
            )
    else:
        change_id = _insert_change(connection, document_id, doc_revision=revision,
                                   base_revision=int(row["objects_revision"]), actor=None, kind="realign")
        _sync(connection, document_id, blocks, revision, None, change_id)
    connection.execute("UPDATE documents SET objects_revision=%s, objects_prefix=%s WHERE id=%s",
                       (revision, prefix, document_id))
    return True


def ensure_committed(connection, document_id: UUID) -> bool:
    """读接口用：对象已是最新时不取写锁；否则在短写事务内回填/对齐并提交。"""
    row = connection.execute("SELECT revision, objects_revision FROM documents WHERE id=%s", (document_id,)).fetchone()
    if row is not None and row["objects_revision"] is not None and int(row["objects_revision"]) >= int(row["revision"]):
        return True
    with connection.transaction(immediate=True):
        return ensure_document_objects(connection, document_id)


def record_commit(connection, document_id: UUID, *, base_revision: int, markdown: str | None, kind: str,
                  actor: UUID | None = None, merged: bool = False, ai_assisted: bool = False,
                  client_save_key: str | None = None, request_hash: str | None = None,
                  identities: list[str | None] | None = None, moved: set[str] | None = None) -> UUID:
    """在 documents 已经 CAS 写成 base_revision+1 之后调用：同步对象、写修改记录、版本操作人。

    markdown=None 表示本次提交没有改正文（例如编写规范设置）。调用方须在写 documents 之前调用 ensure_document_objects。
    """
    actor = current_actor() if actor is None else actor
    from app.project_tasks import guard_locked_chapters  # 30 §3.4 / Q-2：公共框架、任务分解章节只读（40 P2-5）
    guard_locked_chapters(connection, document_id, base_revision, markdown, actor, kind)
    revision = base_revision + 1
    change_id = _insert_change(connection, document_id, doc_revision=revision, base_revision=base_revision, actor=actor,
                               kind=kind, merged=merged, ai_assisted=ai_assisted, client_save_key=client_save_key,
                               request_hash=request_hash)
    row = connection.execute("SELECT objects_revision FROM documents WHERE id=%s", (document_id,)).fetchone()
    changed: int | None = None
    if row is not None and row["objects_revision"] is not None and int(row["objects_revision"]) == base_revision:
        prefix: str | None = None
        if markdown is None:
            changed = 0
        else:
            prefix, blocks = split(markdown)
            if assemble(prefix, blocks) == markdown:
                from app import document_assignments as da
                # A07：没有任何分配、操作人也不受限时不做检查（大文档写锁内不多读一遍对象）
                limited = actor is not None and da.restricted(*da.actor_scope(connection, document_id, actor))
                guard = limited or \
                    connection.execute("SELECT 1 FROM document_object_assignments WHERE document_id=%s LIMIT 1",
                                       (str(document_id),)).fetchone() is not None
                before = da.nodes_of(connection, document_id) if guard or identities is not None else []
                if identities is not None:
                    # A07 §4.1：不可信的 blockIds 逐个核对，不属于本文档 / 类别不符 / 重复的当 null
                    if len(identities) == len(blocks):
                        identities = da.sanitize_identities({n.id: n.kind for n in before}, [b.kind for b in blocks],
                                                            [None if value is None else str(value) for value in identities])
                        if moved is None:
                            moved = da._moved([n.id for n in before], [value for value in identities if value])
                    else:
                        identities = None
                if identities is not None and guard and not limited and \
                        da.identities_drop_assignments(connection, document_id, before, blocks, identities):
                    # 27 M-1：负责人/管理员的块标识会让有分配的对象“消失”而正文仍在（客户端配错）→ 不信这些标识，改走服务端匹配
                    identities, moved = None, None
                changed = _sync(connection, document_id, blocks, revision, actor, change_id, identities, moved)
                # A07 硬限制检查点（17 §4.5.2）：同一写事务内重读分配，越权整体回滚
                if guard:
                    try:
                        da.enforce(connection, document_id, actor, before, revision)
                    except AppError as error:
                        if error.code == "SECTION_NOT_ASSIGNED" and actor is not None:
                            # 28 L-1 / 27 M-3：所有写入路径被拒都留存这次提交（错误处理里另起事务）
                            error.rejected = {"document_id": str(document_id), "actor": str(actor), "base": base_revision,
                                              "markdown": markdown, "block_ids": identities}
                        raise
        if changed is not None:
            connection.execute(
                "UPDATE documents SET objects_revision=%s, objects_prefix=COALESCE(%s, objects_prefix) WHERE id=%s",
                (revision, prefix, document_id),
            )
    if changed is None and markdown is not None and actor is not None:
        from app import document_assignments as da
        if da.restricted(*da.actor_scope(connection, document_id, actor)):
            # 对象模型落后、无法校验分工：对编写人员 fail-closed（17 §4.5.2）
            raise AppError(409, "ASSIGNMENT_CHECK_UNAVAILABLE", "暂时无法核对分工，请刷新后再保存。你的内容已保留在本机。")
    connection.execute("UPDATE documents SET updated_by=%s WHERE id=%s", (actor, document_id))
    connection.execute("UPDATE document_versions SET created_by=%s, changed_objects=%s WHERE document_id=%s AND revision=%s",
                       (actor, changed, document_id, revision))
    return change_id


# ---------------------------------------------------------------- 4.6 REVISION_CONFLICT

def revision_conflict(connection, document_id: UUID, message: str = "文档已更新，请刷新后重试。") -> AppError:
    """严格接口的 409，带最后修改人（4.6）。调用方 raise 返回值；connection=None 时自开只读连接。"""
    if connection is None:
        with connect() as own:
            return revision_conflict(own, document_id, message)
    row = connection.execute(
        """SELECT d.revision, d.updated_at, d.updated_by, u.name AS actor_name
           FROM documents d LEFT JOIN users u ON u.id = d.updated_by WHERE d.id=%s""",
        (document_id,),
    ).fetchone()
    if row is None:
        return AppError(409, "REVISION_CONFLICT", message)
    actor = row["updated_by"]
    me = current_actor()
    details = {
        "lastActor": None if actor is None else {"id": str(actor), "name": row["actor_name"]},
        "lastUpdatedAt": format_timestamp(row["updated_at"]) if row["updated_at"] else None,
        "lastActorIsMe": actor is not None and me is not None and str(actor) == str(me),
    }
    return AppError(409, "REVISION_CONFLICT", message, current_revision=int(row["revision"]), details=details)


# ---------------------------------------------------------------- API

def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(422, "VALIDATION_FAILED", "参数格式不正确。",
                       field_errors=[{"field": field, "message": "格式不正确。"}]) from exc


def _person(user_id, name) -> dict | None:
    return None if user_id is None else {"id": str(user_id), "name": name}


def _changed_by(connection, document_id: UUID, since: int, me: UUID) -> list[dict]:
    rows = connection.execute(
        """SELECT c.actor_id, u.name, COUNT(*) AS objects FROM document_changes c
           JOIN document_object_changes oc ON oc.change_id = c.id
           LEFT JOIN users u ON u.id = c.actor_id
           WHERE c.document_id=%s AND c.doc_revision > %s AND c.actor_id IS NOT NULL AND c.actor_id <> %s
             AND c.kind NOT IN ('backfill','realign')
           GROUP BY c.actor_id, u.name ORDER BY MAX(c.doc_revision) DESC""",
        (document_id, since, me),
    ).fetchall()
    return [{"id": str(row["actor_id"]), "name": row["name"], "objects": int(row["objects"])} for row in rows]


@router.get("/documents/{document_id}/preview/blocks")
def get_preview_blocks(request: Request, document_id: str, offset: int = Query(default=0, ge=0),
                       limit: int = Query(default=500, ge=1, le=5000)) -> dict:
    """43 快速预览：按对象给出 HTML 片段（不经 Word 往返），供编辑区先挂载；整篇版式预览仍走 /preview。"""
    from app.fast_preview import blocks
    principal = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        load_document(conn, did, principal.user_id, is_admin(principal), "read")
        available = ensure_committed(conn, did)
        revision = int(conn.execute("SELECT revision FROM documents WHERE id=%s", (did,)).fetchone()["revision"])
        items = blocks(conn, did, revision, live_objects(conn, did)) if available else []
        page = items[offset:offset + limit]
        return {"data": {"documentRevision": revision, "objectModel": bool(available), "total": len(items),
                         "offset": offset, "limit": limit, "blocks": page,
                         "nextOffset": offset + len(page) if offset + len(page) < len(items) else None},
                "requestId": request.state.request_id}


@router.get("/documents/{document_id}/objects")
def get_objects(request: Request, document_id: str, sinceRevision: int | None = Query(default=None, ge=0)) -> dict:
    principal = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        document = load_document(conn, did, principal.user_id, is_admin(principal), "read")
        # A05 B-8：轮询带 permissions（与文档详情同一来源），权限被调整或项目归档后 45 秒内可感知
        permissions = permissions_of(document.project_role, document.admin, document.archived)
        available = ensure_committed(conn, did)
        revision = int(conn.execute("SELECT revision FROM documents WHERE id=%s", (did,)).fetchone()["revision"])
        if not available:
            return {"data": {"documentRevision": revision, "objectModel": False, "objects": [], "changedBy": [],
                             "permissions": permissions},
                    "requestId": request.state.request_id}
        rows = live_objects(conn, did)
        from app.assignments_api import object_flags, permissions_extra
        flags, assignments_revision, _mode = object_flags(conn, document, principal.user_id)
        permissions = {**permissions, **permissions_extra(conn, document, principal.user_id)}
        names = {str(row["id"]): row["name"] for row in conn.execute(
            """SELECT DISTINCT u.id, u.name FROM document_objects o JOIN users u ON u.id = o.updated_by
               WHERE o.document_id=%s""", (did,)).fetchall()}
        from app.fast_preview import word_heading_levels
        word_levels = word_heading_levels(conn, did, rows)
        objects: list[dict] = []
        previous: UUID | None = None
        for row in rows:
            if sinceRevision is None or int(row["updated_doc_revision"]) > sinceRevision:
                item = {"id": str(row["id"]), "kind": row["kind"], "revision": int(row["revision"]),
                        "markdown": row["body"], "updatedBy": _person(row["updated_by"], names.get(str(row["updated_by"]))),
                        "updatedAt": format_timestamp(row["updated_at"]), "deleted": False,
                        **flags.get(str(row["id"]), {})}
                if row["heading_level"] is not None:
                    item["headingLevel"] = int(row["heading_level"])
                if (word_level := word_levels.get(str(row["id"]))) is not None:
                    # 43：Word 正文样式标题（与整篇预览 data-word-heading 同一判断），编辑区按标题锁定
                    item["isWordHeading"] = True
                    item["wordHeadingLevel"] = word_level
                if sinceRevision is not None:
                    item["afterObjectId"] = None if previous is None else str(previous)
                objects.append(item)
            previous = row["id"]
        if sinceRevision is not None:
            deleted = conn.execute(
                """SELECT o.id, o.kind, o.heading_level, o.revision, o.deleted_at, c.actor_id, u.name, oc.location_json
                   FROM document_objects o
                   LEFT JOIN document_object_changes oc ON oc.object_id = o.id AND oc.op = 'delete'
                   LEFT JOIN document_changes c ON c.id = oc.change_id
                   LEFT JOIN users u ON u.id = c.actor_id
                   WHERE o.document_id=%s AND o.deleted_at IS NOT NULL AND o.deleted_doc_revision > %s
                   ORDER BY o.deleted_doc_revision""",
                (did, sinceRevision),
            ).fetchall()
            for row in deleted:
                objects.append({"id": str(row["id"]), "kind": row["kind"], "revision": int(row["revision"]),
                                "deleted": True, "deletedBy": _person(row["actor_id"], row["name"]),
                                "deletedAt": format_timestamp(row["deleted_at"]),
                                "location": json.loads(row["location_json"]) if row["location_json"] else None})
        changed_by = _changed_by(conn, did, sinceRevision if sinceRevision is not None else revision, principal.user_id)
        return {"data": {"documentRevision": revision, "objectModel": True, "objects": objects, "changedBy": changed_by,
                         "permissions": permissions, "assignmentsRevision": assignments_revision},
                "requestId": request.state.request_id}


def _change_public(row, objects: list | None = None) -> dict:
    value = {"changeId": str(row["id"]), "docRevision": int(row["doc_revision"]), "baseRevision": int(row["base_revision"]),
             "actor": _person(row["actor_id"], row["name"]), "kind": row["kind"], "aiAssisted": bool(row["ai_assisted"]),
             "merged": bool(row["merged"]), "changedObjects": int(row["changed_objects"]),
             "createdAt": format_timestamp(row["created_at"])}
    if objects is not None:
        value["objects"] = objects
    return value


_CHANGE_SELECT = """SELECT c.id, c.doc_revision, c.base_revision, c.actor_id, u.name, c.kind, c.ai_assisted, c.merged,
                           c.created_at, (SELECT COUNT(*) FROM document_object_changes oc WHERE oc.change_id=c.id) AS changed_objects
                    FROM document_changes c LEFT JOIN users u ON u.id = c.actor_id"""


def _object_changes(conn, change_id: UUID, *, bodies: bool) -> list[dict]:
    rows = conn.execute(
        "SELECT object_id, op, before_revision, after_revision, before_body, after_body, location_json "
        "FROM document_object_changes WHERE change_id=%s", (change_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = {"objectId": str(row["object_id"]), "op": row["op"],
                "location": json.loads(row["location_json"]) if row["location_json"] else None}
        if bodies:
            item.update(beforeRevision=row["before_revision"], afterRevision=row["after_revision"],
                        before=row["before_body"], after=row["after_body"])
        result.append(item)
    return result


@router.get("/documents/{document_id}/changes")
def list_changes(request: Request, document_id: str, offset: int = Query(default=0, ge=0),
                 limit: int = Query(default=20, ge=1, le=100), clientSaveKey: str | None = Query(default=None)) -> dict:
    principal = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        load_document(conn, did, principal.user_id, is_admin(principal), "read")
        if clientSaveKey is not None:
            # 只返回本人的（keepalive 保存确认，6.5）
            rows = conn.execute(_CHANGE_SELECT + " WHERE c.document_id=%s AND c.actor_id=%s AND c.client_save_key=%s",
                                (did, principal.user_id, clientSaveKey)).fetchall()
        else:
            rows = conn.execute(_CHANGE_SELECT + """ WHERE c.document_id=%s AND c.kind NOT IN ('backfill','realign')
                                ORDER BY c.doc_revision DESC LIMIT %s OFFSET %s""", (did, limit, offset)).fetchall()
        return {"data": [_change_public(row, _object_changes(conn, row["id"], bodies=False)) for row in rows],
                "requestId": request.state.request_id}


@router.get("/documents/{document_id}/changes/{change_id}")
def get_change(request: Request, document_id: str, change_id: str) -> dict:
    principal = document_principal(request)
    did, cid = _uuid(document_id, "documentId"), _uuid(change_id, "changeId")
    with connect() as conn:
        load_document(conn, did, principal.user_id, is_admin(principal), "read")
        row = conn.execute(_CHANGE_SELECT + " WHERE c.document_id=%s AND c.id=%s", (did, cid)).fetchone()
        if row is None:
            raise AppError(404, "NOT_FOUND", "没有这条修改记录。")
        return {"data": _change_public(row, _object_changes(conn, cid, bodies=True)), "requestId": request.state.request_id}


# ---------------------------------------------------------------- CLI

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.document_objects", description="内容对象往返检查 / 批量回填")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只读：报告每篇文档能否往返切分")
    mode.add_argument("--backfill", action="store_true", help="为所有可往返的文档建立/对齐对象")
    args = parser.parse_args(argv)
    failed: list[str] = []
    with connect(MAINTENANCE_BUSY_MS) as conn:  # 审查 S3：在线回填与保存并发时多等一会，不直接报 locked
        ids = [row["id"] for row in conn.execute("SELECT id FROM documents ORDER BY created_at").fetchall()]
        for did in ids:
            if args.check:
                text = conn.execute("SELECT markdown_content FROM documents WHERE id=%s", (did,)).fetchone()["markdown_content"] or ""
                prefix, blocks = split(text)
                if assemble(prefix, blocks) != text:
                    failed.append(str(did))
                continue
            with conn.transaction(immediate=True):
                if not ensure_document_objects(conn, did):
                    failed.append(str(did))
    print(json.dumps({"documents": len(ids), "roundTripFailed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_cli())


# ---------------------------------------------------------------- 预览对象 ID（01 §4.1，S7）

def decorate_object_ids(connection, document_id: UUID, html: str) -> tuple[str, bool]:
    """给预览的顶层块（p/h1-6/table/pre…，列表取顶层 li）加 data-object-id / data-object-revision。

    只在能证明一一对应时添加：顶层节点数与存活对象数相同，且逐个纯文本一致；否则原样返回，前端走整篇保存。
    返回 (html, objectModel)。
    """
    from lxml import html as html_dom
    from app.markdown_blocks import plain_text

    if not html:
        return html, False
    rows = live_objects(connection, document_id)
    root = html_dom.fragment_fromstring(html, create_parent="div")
    nodes = []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if child.tag in {"ul", "ol"}:
            nodes.extend(item for item in child if isinstance(item.tag, str) and item.tag == "li")
        else:
            nodes.append(child)
    if len(nodes) != len(rows):
        return html, False
    # 工作台重塑轮：比较时去掉全部空白。表格单元格在预览里以空白分隔、plain_text 里直接相连，
    # 原先按“空白归一”比较，含表格的模板文档（几乎全部）都会被判为对不上，工作台误显示“整篇保存”。
    def bare(text: str) -> str:
        return "".join(text.split())

    def node_text(node) -> str:
        if node.tag != "table" or not node.xpath(".//*[@colspan or @rowspan]"):
            return "".join(node.itertext())
        # 与存储的展开表格比较，合并显示不应让整份文档退回整篇保存。
        rows = node.xpath("./thead/tr|./tbody/tr|./tr")
        grid = [{} for _ in rows]
        for r, tr in enumerate(rows):
            column = 0
            for cell in tr.xpath("./th|./td"):
                while column in grid[r]:
                    column += 1
                width = int(cell.get("colspan", "1"))
                height = int(cell.get("rowspan", "1"))
                for y in range(r, min(r + height, len(rows))):
                    for x in range(column, column + width):
                        grid[y][x] = "".join(cell.itertext())
                column += width
        return "".join(row[x] for row in grid for x in sorted(row))

    for node, row in zip(nodes, rows):
        if bare(node_text(node)) != bare(plain_text(row["body"])):
            return html, False
    for node, row in zip(nodes, rows):
        node.set("data-object-id", str(row["id"]))
        node.set("data-object-revision", str(row["revision"]))
    from html import escape
    serialized = escape(root.text or "", quote=False) + "".join(html_dom.tostring(child, encoding="unicode", method="html") for child in root)
    return serialized, True
