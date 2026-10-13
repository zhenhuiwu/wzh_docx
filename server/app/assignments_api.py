"""A07 分工接口（17 §4.6、§10.3）：读取归属、分配、确认继承、分工模式、被拒保存、项目分工汇总。"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app import document_assignments as da
from app.db import connect, write_transaction
from app.document_access import (DocumentRow, document_principal, ensure_project_writable, is_admin, load_document,
                                 project_access)
from app.document_objects import ensure_committed
from app.errors import AppError
from app.markdown_blocks import heading_text, plain_text
from app.models import format_timestamp
from app.project_members import owners_of

router = APIRouter()
MAX_CONTENT_CHARS = 4_000_000


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objectIds: list[str] = Field(min_length=1, max_length=500)
    assigneeIds: list[str] = Field(max_length=50)
    expectedAssignmentsRevision: int = Field(ge=0)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objectIds: list[str] = Field(min_length=1, max_length=500)
    expectedAssignmentsRevision: int = Field(ge=0)


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    expectedAssignmentsRevision: int = Field(ge=0)


class RejectedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = Field(max_length=MAX_CONTENT_CHARS)
    blockIds: list[str | None] | None = Field(default=None, max_length=20000)


def _uuid(value: str, field: str = "documentId") -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(404, "NOT_FOUND", "文档不存在或你无权访问。") from exc


def can_assign(document: DocumentRow) -> bool:
    return (document.admin or document.project_role == "owner") and not document.archived


def _mode(conn, did: UUID) -> tuple[str, int]:
    row = conn.execute("SELECT assignment_mode, assignments_revision FROM documents WHERE id=%s", (str(did),)).fetchone()
    return str(row["assignment_mode"]), int(row["assignments_revision"])


def ownership(conn, document: DocumentRow, actor: UUID) -> dict:
    """归属的唯一计算（resolved、objects、详情 permissions、汇总共用）。"""
    did = document.id
    ensure_committed(conn, did)
    nodes = da.nodes_of(conn, did)
    explicit = da.explicit_map(conn, did)
    owners = da.resolve(nodes, explicit)
    mode, revision = _mode(conn, did)
    actor_id = str(actor)
    names = da._names(conn, {value for value in explicit.values() for value in value})
    resolved = []
    editable_any = False
    editable_headings = []
    levels = {n.id: int(n.level or 1) for n in nodes if n.kind == "heading"}
    for node, owner in zip(nodes, owners):
        ok, reason = da.block_editable(document.project_role, document.admin, mode, document.archived, actor_id, owner)
        editable_any = editable_any or ok
        resolved.append({"objectId": node.id, "kind": node.kind,
                         "owners": [{"id": value, "name": names.get(value, "")} for value in sorted(owner.owners)],
                         "via": owner.via, "viaObjectId": owner.via_id, "editable": ok, "readonlyReason": reason})
        if node.kind == "heading" and ok:
            restricted = da.restricted(document.project_role, document.admin, mode)
            minimum = (levels.get(owner.via_id, 0) + 1) if restricted and owner.via == "heading" and owner.via_id else 1
            editable_headings.append({"headingId": node.id, "minInsertLevel": min(minimum, 6),
                                      "explicit": node.id in explicit})
    return {"nodes": nodes, "owners": owners, "explicit": explicit, "mode": mode, "revision": revision,
            "resolved": resolved, "editableAny": editable_any, "editableHeadings": editable_headings, "names": names}


def _summary(nodes, owners, conn, did) -> dict:
    unassigned = 0
    roots = 0
    stack: list[tuple[int, bool]] = []
    front = 0
    seen_heading = False
    for node, owner in zip(nodes, owners):
        if node.kind != "heading":
            if not seen_heading and not owner.owners:
                front += 1
            continue
        seen_heading = True
        level = int(node.level or 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        empty = not owner.owners
        if empty:
            unassigned += 1
            if not stack or not stack[-1][1]:
                roots += 1
        stack.append((level, empty))
    confirm = conn.execute("SELECT COUNT(*) AS n FROM document_object_assignments WHERE document_id=%s AND needs_confirm=1",
                           (str(did),)).fetchone()["n"]
    return {"unassignedSections": unassigned, "unassignedRoots": roots, "unassignedFrontMatter": front,
            "needsConfirm": int(confirm)}


def project_owners(conn, project_id: UUID) -> list[dict]:
    return [{"id": item["id"], "name": item["name"]} for item in owners_of(conn, [project_id])[str(project_id)]]


def permissions_extra(conn, document: DocumentRow, actor: UUID) -> dict:
    """文档详情 permissions 的 A07 字段（17 §4.6 / §10.6，K-4、K-6）。"""
    view = ownership(conn, document, actor)
    extra = {"assignmentMode": view["mode"], "assignmentsRevision": view["revision"], "canAssign": can_assign(document),
             "editableHeadings": view["editableHeadings"], "projectOwners": project_owners(conn, document.project_id)}
    restricted = da.restricted(document.project_role, document.admin, view["mode"])
    if restricted and not view["editableAny"] and not document.archived:
        extra["readonlyReason"] = "assignment"
    return extra


def _explicit_public(conn, did: UUID) -> list[dict]:
    rows = conn.execute(
        """SELECT a.object_id, a.assignee_id, a.source, a.needs_confirm, a.assigned_by, a.assigned_at,
                  u.name, u.status, m.role
           FROM document_object_assignments a JOIN users u ON u.id = a.assignee_id
           JOIN documents d ON d.id = a.document_id
           LEFT JOIN project_members m ON m.project_id = d.project_id AND m.user_id = a.assignee_id
           WHERE a.document_id=%s ORDER BY a.object_id, a.assigned_at""", (str(did),)).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        item = grouped.setdefault(str(row["object_id"]), {
            "objectId": str(row["object_id"]), "assignees": [], "source": row["source"], "needsConfirm": False,
            "assignedBy": None if row["assigned_by"] is None else str(row["assigned_by"]),
            "assignedAt": format_timestamp(row["assigned_at"]) if row["assigned_at"] else None})
        item["assignees"].append({"id": str(row["assignee_id"]), "name": row["name"], "userActive": row["status"] == "active",
                                  "stillMember": row["role"] is not None, "memberRole": row["role"]})
        item["needsConfirm"] = item["needsConfirm"] or bool(row["needs_confirm"])
    return list(grouped.values())


def _view_payload(conn, document: DocumentRow, actor: UUID, resolved: bool = True) -> dict:
    view = ownership(conn, document, actor)
    revision = int(conn.execute("SELECT revision FROM documents WHERE id=%s", (str(document.id),)).fetchone()["revision"])
    data = {"assignmentMode": view["mode"], "assignmentsRevision": view["revision"], "documentRevision": revision,
            "canAssign": can_assign(document), "projectOwners": project_owners(conn, document.project_id),
            "summary": _summary(view["nodes"], view["owners"], conn, document.id),
            "explicit": _explicit_public(conn, document.id)}
    if resolved:
        data["resolved"] = view["resolved"]
        data["editableHeadings"] = view["editableHeadings"]
    return data


def _load(conn, request: Request, document_id: str, need: str = "read"):
    principal = document_principal(request)
    did = _uuid(document_id)
    document = load_document(conn, did, principal.user_id, is_admin(principal), need)
    return principal, did, document


@router.get("/documents/{document_id}/assignments")
def get_assignments(request: Request, document_id: str, resolved: int = Query(default=0, ge=0, le=1)) -> dict:
    with connect() as conn:
        principal, _did, document = _load(conn, request, document_id)
        return {"data": _view_payload(conn, document, principal.user_id, bool(resolved)), "requestId": request.state.request_id}


def _require_assigner(conn, document: DocumentRow) -> None:
    if not (document.admin or document.project_role == "owner"):
        raise AppError(403, "FORBIDDEN", "只有项目负责人和系统管理员可以分配编写分工。",
                       details={"projectRole": document.project_role, "ownerName": None})
    ensure_project_writable(conn, document.project_id)


def _check_revision(conn, did: UUID, expected: int) -> None:
    _mode_value, revision = _mode(conn, did)
    if revision != expected:
        last = conn.execute(
            """SELECT e.actor_id, u.name, e.created_at FROM document_annotation_events e LEFT JOIN users u ON u.id = e.actor_id
               WHERE e.document_id=%s AND e.kind='assignment' ORDER BY e.created_at DESC, e.rowid DESC LIMIT 1""",
            (str(did),)).fetchone()
        details = {"assignmentsRevision": revision,
                   "lastActor": None if last is None or last["actor_id"] is None else {"id": str(last["actor_id"]), "name": last["name"]},
                   "lastUpdatedAt": None if last is None else format_timestamp(last["created_at"])}
        raise AppError(409, "REVISION_CONFLICT", "分工刚被调整过，已显示最新。", details=details)


def _check_objects(conn, did: UUID, object_ids: list[str]) -> None:
    live = {n.id for n in da.nodes_of(conn, did)}
    stale = [value for value in object_ids if value != da.DOCUMENT and value not in live]
    if stale:
        raise AppError(409, "OBJECT_STALE", "所选内容已被删除或合并，请重新选择。", details={"objectIds": stale[:50]})


def _check_assignees(conn, document: DocumentRow, assignee_ids: list[str]) -> None:
    for value in assignee_ids:
        row = conn.execute(
            """SELECT u.status, m.role FROM users u LEFT JOIN project_members m ON m.user_id = u.id AND m.project_id = %s
               WHERE u.id = %s""", (str(document.project_id), value)).fetchone()
        reason = None
        if row is None or row["role"] is None:
            reason = "not_member"
        elif row["status"] != "active":
            reason = "disabled"
        elif row["role"] not in ("owner", "editor"):
            reason = "readonly"
        if reason:
            text = {"not_member": "该账号已不是项目成员", "disabled": "该账号已停用", "readonly": "只读成员不能分配编写"}[reason]
            raise AppError(422, "ASSIGNEE_NOT_ELIGIBLE", f"{text}。", details={"userId": value, "reason": reason},
                           field_errors=[{"field": "assigneeIds", "message": text}])


@router.put("/documents/{document_id}/assignments")
def put_assignments(request: Request, document_id: str, body: AssignBody) -> dict:
    object_ids = list(dict.fromkeys(body.objectIds))
    assignee_ids = list(dict.fromkeys(body.assigneeIds))
    for value in assignee_ids:
        _uuid(value, "assigneeIds")
    with connect() as conn:
        principal, did, document = _load(conn, request, document_id)
        _require_assigner(conn, document)
        with write_transaction(conn):
            _check_revision(conn, did, body.expectedAssignmentsRevision)
            ensure_committed_in_tx(conn, did)
            _check_objects(conn, did, object_ids)
            _check_assignees(conn, document, assignee_ids)
            for object_id in object_ids:
                conn.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s", (str(did), object_id))
                for assignee in assignee_ids:
                    conn.execute(
                        """INSERT INTO document_object_assignments (document_id, object_id, assignee_id, source, needs_confirm,
                               assigned_by, assigned_at) VALUES (%s,%s,%s,'manual',0,%s,now())""",
                        (str(did), object_id, assignee, str(principal.user_id)))
            da.bump(conn, did)
            da.event(conn, did, "assignment", "assign" if assignee_ids else "unassign", object_ids, None, principal.user_id,
                     payload={"assigneeIds": assignee_ids})
        return {"data": _view_payload(conn, document, principal.user_id), "requestId": request.state.request_id}


def ensure_committed_in_tx(conn, did: UUID) -> None:
    from app.document_objects import ensure_document_objects
    ensure_document_objects(conn, did)


@router.post("/documents/{document_id}/assignments:confirm")
def confirm_assignments(request: Request, document_id: str, body: ConfirmBody) -> dict:
    with connect() as conn:
        principal, did, document = _load(conn, request, document_id)
        _require_assigner(conn, document)
        with write_transaction(conn):
            _check_revision(conn, did, body.expectedAssignmentsRevision)
            for object_id in body.objectIds:
                conn.execute("UPDATE document_object_assignments SET needs_confirm=0 WHERE document_id=%s AND object_id=%s",
                             (str(did), object_id))
            da.bump(conn, did)
            da.event(conn, did, "assignment", "confirm", list(body.objectIds), None, principal.user_id)
        return {"data": _view_payload(conn, document, principal.user_id), "requestId": request.state.request_id}


@router.put("/documents/{document_id}/assignment-mode")
def put_mode(request: Request, document_id: str, body: ModeBody) -> dict:
    if body.mode not in ("open", "enforced"):
        raise AppError(422, "VALIDATION_FAILED", "分工模式只能是 open 或 enforced。",
                       field_errors=[{"field": "mode", "message": "分工模式不正确。"}])
    with connect() as conn:
        principal, did, document = _load(conn, request, document_id)
        _require_assigner(conn, document)
        with write_transaction(conn):
            _check_revision(conn, did, body.expectedAssignmentsRevision)
            current, _revision = _mode(conn, did)
            if current != body.mode:
                conn.execute("UPDATE documents SET assignment_mode=%s WHERE id=%s", (body.mode, str(did)))
                da.bump(conn, did)
                da.event(conn, did, "assignment", "mode_on" if body.mode == "enforced" else "mode_off", [], None, principal.user_id)
            mode, revision = _mode(conn, did)
        return {"data": {"assignmentMode": mode, "assignmentsRevision": revision}, "requestId": request.state.request_id}


def _rejected(conn, did: UUID, rid: str, actor: UUID):
    row = conn.execute("SELECT * FROM document_rejected_saves WHERE id=%s AND document_id=%s AND actor_id=%s",
                       (rid, str(did), str(actor))).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这份被拒的保存。")
    return row


@router.get("/documents/{document_id}/rejected-saves/{rid}")
def get_rejected(request: Request, document_id: str, rid: str) -> dict:
    with connect() as conn:
        principal, did, _document = _load(conn, request, document_id)
        row = _rejected(conn, did, rid, principal.user_id)
        return {"data": {"id": rid, "markdown": row["markdown"], "blockIds": json.loads(row["block_ids"]) if row["block_ids"] else None,
                         "violations": json.loads(row["violations"]), "baseRevision": int(row["base_revision"]),
                         "createdAt": format_timestamp(row["created_at"]), "updatedAt": format_timestamp(row["updated_at"])},
                "requestId": request.state.request_id}


@router.put("/documents/{document_id}/rejected-saves/{rid}")
def put_rejected(request: Request, document_id: str, rid: str, body: RejectedBody) -> dict:
    """暂存同步（17 §10.7）：只更新本人最新的一份；不校验分工、不改正文、不增加份数、不重置 30 天。"""
    with connect() as conn:
        principal, did, _document = _load(conn, request, document_id)
        with write_transaction(conn):
            _rejected(conn, did, rid, principal.user_id)
            latest = conn.execute(
                """SELECT id FROM document_rejected_saves WHERE document_id=%s AND actor_id=%s
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""", (str(did), str(principal.user_id))).fetchone()
            if latest is None or str(latest["id"]) != rid:
                raise AppError(404, "NOT_FOUND", "只能更新最新一份被拒的保存。")
            conn.execute("UPDATE document_rejected_saves SET markdown=%s, block_ids=%s, updated_at=now() WHERE id=%s",
                         (body.markdown, None if body.blockIds is None else json.dumps(body.blockIds), rid))
            updated = conn.execute("SELECT updated_at FROM document_rejected_saves WHERE id=%s", (rid,)).fetchone()
        return {"data": {"updatedAt": format_timestamp(updated["updated_at"])}, "requestId": request.state.request_id}


@router.get("/projects/{project_id}/assignment-summary")
def assignment_summary(request: Request, project_id: str) -> dict:
    """K-1（17 §10.3）：按文档 / 按人。按标题计数，不代表编写完成度。"""
    principal = document_principal(request)
    pid = _uuid(project_id, "projectId")
    with connect() as conn:
        project_access(conn, principal, pid, "read")
        documents = conn.execute("SELECT id, name, assignment_mode, assignments_revision, revision FROM documents "
                                 "WHERE project_id=%s ORDER BY created_at", (str(pid),)).fetchall()
        users = {str(row["id"]): row for row in conn.execute(
            """SELECT u.id, u.name, u.status, m.role FROM project_members m JOIN users u ON u.id = m.user_id
               WHERE m.project_id=%s""", (str(pid),)).fetchall()}
        by_document = []
        people: dict[str, dict] = {}
        for doc in documents:
            ensure_committed(conn, doc["id"])
            nodes = da.nodes_of(conn, doc["id"])
            explicit = da.explicit_map(conn, doc["id"])
            owners = da.resolve(nodes, explicit)
            summary = _summary(nodes, owners, conn, doc["id"])
            kinds = {n.id: n.kind for n in nodes}
            headings = [owner for node, owner in zip(nodes, owners) if node.kind == "heading"]
            participants = sorted({value for value in explicit.values() for value in value})
            exceptions = sum(1 for key in explicit if key != da.DOCUMENT and kinds.get(key) not in (None, "heading"))
            by_document.append({
                "documentId": str(doc["id"]), "name": doc["name"], "assignmentMode": doc["assignment_mode"],
                "sectionsTotal": len(headings), "sectionsAssigned": sum(1 for owner in headings if owner.owners),
                **summary, "exceptions": exceptions, "hasDocumentAssignment": da.DOCUMENT in explicit,
                "documentOwners": [{"id": value, "name": users[value]["name"] if value in users else ""}
                                   for value in sorted(explicit.get(da.DOCUMENT, frozenset()))],
                "participants": [{"id": value, "name": users[value]["name"] if value in users else "",
                                  "userActive": value in users and users[value]["status"] == "active"} for value in participants],
                "assignmentsRevision": int(doc["assignments_revision"]), "documentRevision": int(doc["revision"])})
            for key, assignees in explicit.items():
                if key != da.DOCUMENT and key not in kinds:
                    continue
                for value in assignees:
                    person = people.setdefault(value, {"sections": 0, "blocks": 0, "items": {}})
                    item = person["items"].setdefault(str(doc["id"]), {"documentId": str(doc["id"]), "name": doc["name"],
                                                                         "sections": 0, "blocks": 0, "whole": False})
                    if key == da.DOCUMENT:
                        item["whole"] = True
                        person["sections"] += 1
                        item["sections"] += 1
                    elif kinds[key] == "heading":
                        person["sections"] += 1
                        item["sections"] += 1
                    else:
                        person["blocks"] += 1
                        item["blocks"] += 1
        by_person = []
        for value, person in people.items():
            row = users.get(value)
            by_person.append({"userId": value, "name": row["name"] if row else "", "role": row["role"] if row else None,
                              "userActive": bool(row) and row["status"] == "active", "sections": person["sections"],
                              "blocks": person["blocks"], "documents": len(person["items"]), "items": list(person["items"].values())})
        by_person.sort(key=lambda item: item["name"])
        unassigned = [{"userId": key, "name": row["name"]} for key, row in users.items()
                      if row["role"] == "editor" and row["status"] == "active" and key not in people]
        me = users.get(str(principal.user_id))
        can = is_admin(principal) or bool(me and me["role"] == "owner")
        return {"data": {"byDocument": by_document, "byPerson": by_person, "unassignedEditors": unassigned, "canAssign": can},
                "requestId": request.state.request_id}


def object_flags(conn, document: DocumentRow, actor: UUID) -> tuple[dict[str, dict], int, str]:
    """objects 轮询：每个对象 editable / owners（与 resolved 同一计算）。"""
    view = ownership(conn, document, actor)
    flags = {item["objectId"]: {"editable": item["editable"], "owners": item["owners"]} for item in view["resolved"]}
    return flags, view["revision"], view["mode"]


def excerpt(node) -> str:
    return (heading_text(node.body) if node.kind == "heading" else plain_text(node.body))[:40]
