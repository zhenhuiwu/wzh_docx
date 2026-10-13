"""文档对象分工。未启用的旧文档保持原权限；分工不随清空或成员移除自动关闭。"""

from __future__ import annotations

from collections import defaultdict
import json
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect, write_transaction
from app.document_access import document_principal, is_admin, load_document
from app.document_objects import current_actor, ensure_committed, ensure_document_objects, live_objects
from app.errors import AppError
from app.markdown_blocks import split

router = APIRouter()


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "文档不存在或你无权访问。") from None


def _access(connection, did, actor_id):
    user = connection.execute("SELECT status FROM users WHERE id=%s", (actor_id,)).fetchone()
    if user is None or user["status"] != "active":
        raise AppError(403, "FORBIDDEN", "当前账号不能修改文档。")
    admin = connection.execute("SELECT 1 FROM user_roles WHERE user_id=%s AND role='system_admin'", (actor_id,)).fetchone()
    return load_document(connection, did, actor_id, bool(admin), "edit")


def require_manage(connection, did, actor_id) -> None:
    row = _access(connection, did, actor_id)
    if not row.admin and row.project_role != "owner":
        raise AppError(403, "ASSIGNMENT_FORBIDDEN", "请由项目负责人管理文档分工和全篇设置。")


def require_scope_write(connection, did, actor_id, object_ids=None) -> None:
    row = _access(connection, did, actor_id)
    settings = connection.execute("SELECT assignments_enabled FROM documents WHERE id=%s", (did,)).fetchone()
    if row.admin or row.project_role == "owner" or not settings["assignments_enabled"]:
        return
    ensure_document_objects(connection, did)
    live = live_objects(connection, did)
    ids = {str(item["id"]) for item in live} if object_ids is None else {str(value) for value in object_ids}
    live_ids = {str(item["id"]) for item in live}
    owned = {str(item["object_id"]) for item in connection.execute(
        "SELECT object_id FROM document_object_assignments WHERE document_id=%s AND assignee_id=%s", (did, actor_id)).fetchall()}
    if not ids <= live_ids or not ids <= owned:
        raise AppError(403, "ASSIGNMENT_FORBIDDEN", "只能修改分配给你的内容，请联系项目负责人调整分工。",
                       details={"objectIds": sorted(ids - owned), "readonlyReason": "assignment"})


def check_content_write(connection, did, markdown: str, *, identities=None, moved=None) -> None:
    """所有正文保存共用的事务内闸口；分工限制开启后拒绝无法可靠定位的结构变更。"""
    # 先获得写锁，防止检查后分工或项目角色被另一事务修改。
    connection.execute("UPDATE documents SET assignments_revision=assignments_revision WHERE id=%s", (did,))
    actor = current_actor()
    settings = connection.execute("SELECT assignments_enabled, markdown_content FROM documents WHERE id=%s", (did,)).fetchone()
    if settings is None:
        raise AppError(404, "NOT_FOUND", "文档不存在。")
    if actor is None:
        if settings["assignments_enabled"]:
            raise AppError(403, "ASSIGNMENT_FORBIDDEN", "分工文档的修改需要明确操作账号。")
        return
    row = _access(connection, did, actor)
    if row.admin or row.project_role == "owner" or not settings["assignments_enabled"]:
        return
    ensure_document_objects(connection, did)
    before = live_objects(connection, did)
    prefix, after = split(markdown)
    old_prefix, _ = split(settings["markdown_content"] or "")
    # shortcut: 分工人员只做原位修改；支持拆分/合并前须定义新对象的分工继承。
    structural = (prefix != old_prefix or len(before) != len(after) or bool(moved))
    if identities is not None and identities != [str(item["id"]) for item in before]:
        structural = True
    old_positions = defaultdict(set)
    for index, item in enumerate(before):
        old_positions[(item["kind"], item["body"])].add(index)
    changed = []
    for index, (old, new) in enumerate(zip(before, after)):
        if old["kind"] != new.kind or old["heading_level"] != new.heading_level:
            structural = True
        if (old["body"], old["gap_after"]) != (new.body, new.gap_after):
            changed.append(str(old["id"]))
            if old["kind"] == "heading" or (old_positions[(new.kind, new.body)] and index not in old_positions[(new.kind, new.body)]):
                structural = True
    if structural:
        raise AppError(403, "ASSIGNMENT_STRUCTURE_FORBIDDEN", "分工编写时可修改分配的正文和表格；新增、删除、移动内容或修改标题请由项目负责人处理。")
    require_scope_write(connection, did, actor, changed)


def record_assignment_event(connection, did, actor_id, changes) -> None:
    connection.execute("UPDATE documents SET assignments_revision=assignments_revision+1 WHERE id=%s", (did,))
    connection.execute(
        "INSERT INTO document_assignment_events(document_id,assignments_revision,actor_id,changes_json) "
        "SELECT id,assignments_revision,%s,%s FROM documents WHERE id=%s",
        (actor_id, json.dumps(changes, ensure_ascii=False), did))


def _snapshot(connection, did, actor):
    row = load_document(connection, did, actor.user_id, is_admin(actor))
    settings = connection.execute("SELECT assignments_enabled,assignments_revision FROM documents WHERE id=%s", (did,)).fetchone()
    manage = not row.archived and (row.admin or row.project_role == "owner")
    editing = not row.archived and (row.admin or row.project_role in {"owner", "editor"})
    assigned = {str(item["object_id"]): item for item in connection.execute(
        "SELECT a.object_id,a.assignee_id,u.name FROM document_object_assignments a JOIN users u ON u.id=a.assignee_id WHERE a.document_id=%s", (did,)).fetchall()}
    items = []
    for item in live_objects(connection, did):
        assignment = assigned.get(str(item["id"]))
        assignee = str(assignment["assignee_id"]) if assignment else None
        items.append({"objectId": str(item["id"]), "kind": item["kind"], "text": item["body"],
                      "assigneeId": assignee, "assigneeName": assignment["name"] if assignment else None,
                      "canEdit": editing and (manage or not settings["assignments_enabled"] or
                                              (assignee == str(actor.user_id) and item["kind"] != "heading"))})
    members = connection.execute(
        "SELECT u.id,u.name FROM project_members m JOIN users u ON u.id=m.user_id "
        "WHERE m.project_id=%s AND m.role IN ('owner','editor') AND u.status='active' ORDER BY u.name,u.account", (row.project_id,)).fetchall()
    return {"revision": row.revision, "assignmentsRevision": settings["assignments_revision"],
            "enabled": bool(settings["assignments_enabled"]), "canManage": manage, "items": items,
            "members": [{"id": str(user["id"]), "name": user["name"]} for user in members]}


class AssignmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objectId: UUID
    assigneeId: UUID | None


class AssignmentPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    expectedAssignmentsRevision: int = Field(ge=0)
    enabled: bool | None = None
    items: list[AssignmentItem] = Field(default_factory=list, max_length=5000)


@router.get("/documents/{document_id}/assignments")
def get_assignments(request: Request, document_id: str):
    actor, did = document_principal(request), _uuid(document_id)
    with connect() as connection:
        load_document(connection, did, actor.user_id, is_admin(actor))
        ensure_committed(connection, did)
        return {"data": _snapshot(connection, did, actor), "requestId": request.state.request_id}


@router.put("/documents/{document_id}/assignments")
def put_assignments(request: Request, document_id: str, body: AssignmentPut):
    actor, did = document_principal(request), _uuid(document_id)
    with connect() as connection, write_transaction(connection):
        require_manage(connection, did, actor.user_id)
        ensure_document_objects(connection, did)
        current = _snapshot(connection, did, actor)
        if current["revision"] != body.expectedRevision or current["assignmentsRevision"] != body.expectedAssignmentsRevision:
            raise AppError(409, "ASSIGNMENT_CONFLICT", "正文或分工已更新，请刷新后重新分配。", current_revision=current["revision"],
                           details={"assignmentsRevision": current["assignmentsRevision"]})
        available = {item["objectId"]: item for item in current["items"]}
        members = {member["id"] for member in current["members"]}
        seen = set()
        changes = []
        for item in body.items:
            oid, uid = str(item.objectId), str(item.assigneeId) if item.assigneeId else None
            if oid not in available or oid in seen:
                raise AppError(422, "ASSIGNMENT_OBJECT_INVALID", "分配对象不存在、已删除或重复，请刷新正文。")
            seen.add(oid)
            if uid is not None and uid not in members:
                raise AppError(422, "ASSIGNEE_NOT_ELIGIBLE", "只能分配给本项目中已启用的负责人或编写人员。")
            previous = available[oid]["assigneeId"]
            if previous == uid:
                continue
            if uid is None:
                connection.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s", (did, oid))
            else:
                connection.execute(
                    "INSERT INTO document_object_assignments(document_id,object_id,assignee_id,updated_by) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT(document_id,object_id) DO UPDATE SET assignee_id=excluded.assignee_id,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP",
                    (did, oid, uid, actor.user_id))
            changes.append({"objectId": oid, "before": previous, "after": uid})
        enabled = body.enabled if body.enabled is not None else (current["enabled"] or any(item.assigneeId for item in body.items))
        if enabled != current["enabled"]:
            connection.execute("UPDATE documents SET assignments_enabled=%s WHERE id=%s", (int(enabled), did))
            changes.append({"enabled": bool(enabled)})
        if changes:
            record_assignment_event(connection, did, actor.user_id, changes)
        return {"data": _snapshot(connection, did, actor), "requestId": request.state.request_id}
