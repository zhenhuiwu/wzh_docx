"""A05 项目成员与权限（17 §2；16 §2）。

规则（17 §2.3，均在同一个 BEGIN IMMEDIATE 写事务里执行，SQLite 单写者保证计数与写入之间不插入其他写事务）：
- R1 最后负责人：只统计 users.status='active' 的 owner；只拦会把启用负责人数降到 0 的动作（降级/移除一个启用 owner）。
  降级或移除已停用的 owner 永远允许（历史数据里“唯一 owner 已停用”的项目不会卡死）。→ 409 LAST_OWNER
- R3 归档：归档项目的成员写操作一律 409 PROJECT_ARCHIVED；读照常，canManageMembers=false、readonlyReason='archived'。
- R4 主负责人（projects.owner_id）：被降级/移除、或已不是启用 owner 时，改指到剩余启用 owner 中加入最早的一位，
  写 owner_reassigned 审计；提升别人为负责人不再自动改主负责人。
- R5 成员独立版本号 projects.members_revision：成员接口只校验和递增它，不再动 projects.revision。
  PUT body / DELETE query 的 expectedMembersRevision 必填；DELETE 目标不是成员时 204，不加版本、不写审计。
- R6 停用账号：设置任何角色（加入或提升/平级）→ 422 USER_DISABLED；已在项目中的停用账号可以被降级或移除。
- R7 reviewer：界面不再分配，PUT 不接受 role=reviewer（422）；已有数据照常返回，reviews.decide 不变（U-4 后半）。
- 编写人员和只读成员不能自己退出项目（U-6）：成员写接口只放行系统管理员和本项目负责人。
- 每次变更写 project_member_events 审计。
- 与 A07 的联动（移出/降为只读时清理分工）见 app/member_assignments.py。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import member_assignments
from app.db import connect, write_transaction
from app.document_access import (PROJECT_ARCHIVED_MESSAGE, active_owner_names, member_role, owner_names_text)
from app.errors import AppError
from app.identity import require_csrf, require_session
from app.models import format_timestamp
from app.permissions import AUDIT_PROJECT_ROLE

router = APIRouter()

ASSIGNABLE_ROLES = ("owner", "editor", "viewer")
ALL_ROLES = frozenset({"owner", "editor", "reviewer", "viewer"})
ROLE_RANK = {"owner": 3, "editor": 2, "reviewer": 1, "viewer": 1}
ROLE_TEXT = {"owner": "负责人", "editor": "编写人员", "viewer": "只读成员", "reviewer": "审核人员（只读）"}
LAST_OWNER_MESSAGE = "项目至少需要一位启用中的负责人。请先把其他成员设为负责人。"
BATCH_LIMIT = 50
CANDIDATE_PAGE_LIMIT = 50
SOLE_OWNER_LIST_LIMIT = 20


class MemberPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    expectedMembersRevision: int | None = Field(default=None, ge=0)
    userId: str | None = None
    # 兼容已部署的旧前端一个版本：接受但忽略（17 §2.3 R5）
    expectedProjectRevision: int | None = None


class MemberBatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userIds: list[str]
    role: str
    expectedMembersRevision: int | None = Field(default=None, ge=0)


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise AppError(422, "VALIDATION_FAILED", "标识不正确。", [{"field": field, "message": "需要有效标识。"}]) from None


def _require_revision(value: int | None) -> int:
    if value is None:
        raise AppError(422, "VALIDATION_FAILED", "缺少成员名单版本号，请刷新后再试。",
                       [{"field": "expectedMembersRevision", "message": "需要填写。"}])
    return value


def _assignable_role(role: str) -> str:
    if role == "reviewer":
        raise AppError(422, "VALIDATION_FAILED", "审核人员已不再分配，请选择负责人、编写人员或只读成员。",
                       [{"field": "role", "message": "请选择负责人、编写人员或只读成员。"}])
    if role not in ASSIGNABLE_ROLES:
        raise AppError(422, "VALIDATION_FAILED", "项目权限不正确。", [{"field": "role", "message": "请选择负责人、编写人员或只读成员。"}])
    return role


def _is_admin(actor) -> bool:
    return "system_admin" in actor.roles


def _reads_all(actor) -> bool:
    return "system_admin" in actor.roles or "auditor" in actor.roles


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _visible_project(connection, project_id: UUID, actor) -> dict:
    """非成员（且不是管理员/审计员）一律 404，与项目不存在相同，不泄露存在性（S1/I7）。"""
    row = connection.execute(
        "select id, name, owner_id, archived_at, members_revision from projects where id = %s", (project_id,)
    ).fetchone()
    role = None if row is None else member_role(connection, project_id, actor.user_id)
    if row is None or (role is None and not _reads_all(actor)):
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
    # member_role 对非成员审计员返回 "auditor"；管理员非成员为 None
    return {"id": row["id"], "name": row["name"], "ownerId": row["owner_id"], "archived": row["archived_at"] is not None,
            "membersRevision": int(row["members_revision"]), "role": role}


def _manages(actor, project: dict) -> bool:
    return _is_admin(actor) or project["role"] == "owner"


def _require_manager(connection, actor, project: dict, action: str = "调整成员") -> None:
    if _manages(actor, project):
        return
    role = project["role"]
    if role == AUDIT_PROJECT_ROLE:
        raise AppError(403, "FORBIDDEN", f"你以审计身份只读查看该项目，不能{action}。",
                       details={"projectRole": role, "ownerName": None})
    owner = owner_names_text(active_owner_names(connection, project["id"]))
    tail = f"如需开通请联系负责人 {owner}。" if owner else "如需开通请联系项目负责人。"
    raise AppError(403, "FORBIDDEN", f"你在该项目中没有此操作权限，{tail}",
                   details={"projectRole": role, "ownerName": owner})


def _require_not_archived(project: dict) -> None:
    if project["archived"]:
        raise AppError(409, "PROJECT_ARCHIVED", "项目已归档，成员和权限不能调整。恢复项目后才能修改。",
                       details={"readonlyReason": "archived"})


def _last_event(connection, project_id: UUID) -> dict | None:
    row = connection.execute(
        """select e.actor_id, u.name, e.created_at from project_member_events e
           left join users u on u.id = e.actor_id
           where e.project_id = %s order by e.created_at desc, e.rowid desc limit 1""",
        (project_id,),
    ).fetchone()
    return None if row is None else row


def _check_members_revision(connection, project: dict, expected: int) -> None:
    current = project["membersRevision"]
    if current == expected:
        return
    last = _last_event(connection, project["id"])
    details = {
        "currentRevision": current,
        "lastActor": None if last is None or last["actor_id"] is None else {"id": str(last["actor_id"]), "name": last["name"]},
        "lastUpdatedAt": None if last is None else format_timestamp(last["created_at"]),
    }
    raise AppError(409, "REVISION_CONFLICT", "成员名单刚被调整过，请刷新后再修改。", current_revision=current, details=details)


def _active_owner_count(connection, project_id: UUID, exclude: UUID | None = None) -> int:
    row = connection.execute(
        """select count(*) as total from project_members m join users u on u.id = m.user_id
           where m.project_id = %s and m.role = 'owner' and u.status = 'active'
             and (%s is null or m.user_id <> %s)""",
        (project_id, exclude, exclude),
    ).fetchone()
    return int(row["total"])


def owners_of(connection, project_ids: list[UUID]) -> dict[str, list[dict[str, object]]]:
    """{projectId: owners[{id,name,status,isPrimary}]}：owners[0] 固定是主负责人（projects.owner_id 仍是 owner 时），
    其余按加入时间（无时间排后）、姓名、账号。含停用的负责人（status='disabled'），页头由前端只显示启用的。"""
    if not project_ids:
        return {}
    marks = ",".join(["%s"] * len(project_ids))
    rows = connection.execute(
        f"""select m.project_id, u.id, u.name, u.status, (m.user_id = p.owner_id) as is_primary
            from project_members m
            join users u on u.id = m.user_id
            join projects p on p.id = m.project_id
            where m.role = 'owner' and m.project_id in ({marks})
            order by m.project_id, is_primary desc,
                     case when m.created_at is null then 1 else 0 end, m.created_at, u.name, u.account""",
        list(project_ids),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = {str(pid): [] for pid in project_ids}
    for row in rows:
        result[str(row["project_id"])].append({"id": str(row["id"]), "name": row["name"], "status": row["status"],
                                               "isPrimary": bool(row["is_primary"])})
    return result


def _member_rows(connection, project_id: UUID, filters: list[str] | None = None, params: list[object] | None = None):
    where = " and ".join(["m.project_id = %s", *(filters or [])])
    return connection.execute(
        f"""select m.user_id, u.name, u.account, u.department, u.status, m.role, m.revision,
                   m.created_at, m.updated_at, m.updated_by, ub.name as updated_by_name
            from project_members m
            join users u on u.id = m.user_id
            left join users ub on ub.id = m.updated_by
            where {where}
            order by case m.role when 'owner' then 0 when 'editor' then 1 when 'reviewer' then 2 else 3 end,
                     u.name, u.account, m.user_id""",
        [project_id, *(params or [])],
    ).fetchall()


def _member_public(row, actor, counts: dict[str, dict[str, int]]) -> dict[str, object]:
    user_id = str(row["user_id"])
    return {
        "userId": user_id,
        "name": row["name"],
        "account": row["account"],
        "department": row["department"],
        "userStatus": row["status"],
        "role": row["role"],
        "isMe": user_id == str(actor.user_id),
        "addedAt": None if row["created_at"] is None else format_timestamp(row["created_at"]),
        "updatedAt": None if row["updated_at"] is None else format_timestamp(row["updated_at"]),
        "updatedBy": None if row["updated_by"] is None else {"id": str(row["updated_by"]), "name": row["updated_by_name"]},
        "revision": int(row["revision"]),
        # A07 挂钩（K-5）：显式分工数；A07 未上线时为 0
        "assignments": counts.get(user_id, member_assignments.empty()),
    }


def _snapshot(connection, project_id: UUID, actor, filters: list[str] | None = None,
              params: list[object] | None = None) -> dict[str, object]:
    project = _visible_project(connection, project_id, actor)
    counts = member_assignments.assignment_counts(connection, project_id)
    items = [_member_public(row, actor, counts) for row in _member_rows(connection, project_id, filters, params)]
    manages = _manages(actor, project)
    can_manage = manages and not project["archived"]
    return {
        "items": items,
        "membersRevision": project["membersRevision"],
        "activeOwnerCount": _active_owner_count(connection, project_id),
        "owners": owners_of(connection, [project_id])[str(project_id)],
        "ownerId": str(project["ownerId"]),
        "canManageMembers": can_manage,
        "readonlyReason": None if can_manage else ("archived" if project["archived"] else "role"),
        "projectRole": project["role"],
    }


def _record(connection, project_id: UUID, actor_id: UUID | None, target: UUID, action: str,
            old_role: str | None, new_role: str | None, revision: int, request_id: str | None) -> None:
    connection.execute(
        """insert into project_member_events
             (id, project_id, actor_id, target_user_id, action, old_role, new_role, members_revision, request_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (uuid4(), project_id, actor_id, target, action, old_role, new_role, revision, request_id),
    )


def _bump(connection, project_id: UUID) -> int:
    connection.execute("update projects set members_revision = members_revision + 1 where id = %s", (project_id,))
    return int(connection.execute("select members_revision from projects where id = %s", (project_id,)).fetchone()["members_revision"])


def _person(connection, user_id) -> dict[str, str] | None:
    if user_id is None:
        return None
    row = connection.execute("select id, name from users where id = %s", (user_id,)).fetchone()
    return None if row is None else {"id": str(row["id"]), "name": row["name"]}


def reassign_primary(connection, project_id: UUID, actor_id: UUID | None, revision: int,
                     request_id: str | None) -> dict[str, object] | None:
    """R4：owner_id 已不是启用 owner 时改指到剩余启用 owner 中加入最早的一位；没有启用 owner 时保持不变。"""
    current = connection.execute("select owner_id from projects where id = %s", (project_id,)).fetchone()["owner_id"]
    rows = connection.execute(
        """select m.user_id from project_members m join users u on u.id = m.user_id
           where m.project_id = %s and m.role = 'owner' and u.status = 'active'
           order by case when m.created_at is null then 1 else 0 end, m.created_at, u.name, u.account""",
        (project_id,),
    ).fetchall()
    active = [row["user_id"] for row in rows]
    if not active or current in active:
        return None
    target = active[0]
    connection.execute("update projects set owner_id = %s where id = %s", (target, project_id))
    _record(connection, project_id, actor_id, target, "owner_reassigned", None, "owner", revision, request_id)
    return {"from": _person(connection, current), "to": _person(connection, target)}


def _result(connection, project_id: UUID, actor, *, owner_changed, cleared, extra: dict | None = None) -> dict[str, object]:
    snapshot = _snapshot(connection, project_id, actor)
    return {**snapshot, "ownerChanged": owner_changed, "assignmentsCleared": cleared, **(extra or {})}


def _write_actor(request: Request):
    actor = require_session(request)
    require_csrf(request, actor)
    return actor


@router.get("/projects/{project_id}/members")
def list_members(project_id: str, request: Request, name: str = "", account: str = "", role: str | None = None) -> dict[str, object]:
    actor = require_session(request)  # S1：成员都能看（P-3）；非成员 404
    target = _uuid(project_id, "projectId")
    if role is not None and role not in ALL_ROLES:
        raise AppError(422, "VALIDATION_FAILED", "项目角色不正确。")
    filters: list[str] = []
    params: list[object] = []
    if name.strip():
        filters.append("u.name like %s escape '\\'")
        params.append(_like(name.strip()))
    if account.strip():
        filters.append("u.account_key like %s escape '\\'")
        params.append(_like(account.strip().casefold()))
    if role:
        filters.append("m.role = %s")
        params.append(role)
    with connect() as connection:
        data = _snapshot(connection, target, actor, filters, params)
    return {"data": data, "requestId": request.state.request_id}


@router.put("/projects/{project_id}/members/{user_id}")
def set_member(project_id: str, user_id: str, request: Request, body: MemberPutBody) -> dict[str, object]:
    actor = _write_actor(request)
    target = _uuid(project_id, "projectId")
    member_id = _uuid(user_id, "userId")
    if body.userId is not None and _uuid(body.userId, "userId") != member_id:
        raise AppError(422, "VALIDATION_FAILED", "成员信息不正确。", [{"field": "userId", "message": "与地址中的账号不一致。"}])
    role = _assignable_role(body.role)
    expected = _require_revision(body.expectedMembersRevision)
    request_id = getattr(request.state, "request_id", None)
    with connect() as connection, write_transaction(connection):
        project = _visible_project(connection, target, actor)
        _require_manager(connection, actor, project)
        _require_not_archived(project)
        _check_members_revision(connection, project, expected)
        user = connection.execute("select id, name, status from users where id = %s", (member_id,)).fetchone()
        if user is None:
            raise AppError(404, "NOT_FOUND", "没有这个账号。")
        existing = connection.execute(
            "select role from project_members where project_id = %s and user_id = %s", (target, member_id)
        ).fetchone()
        old_role = None if existing is None else str(existing["role"])
        if old_role == role:
            # 没有变化：不加版本、不写审计
            return {"data": _result(connection, target, actor, owner_changed=None, cleared=member_assignments.empty(),
                                    extra={"item": _item(connection, target, member_id, actor)}),
                    "requestId": request_id}
        if user["status"] != "active" and (old_role is None or ROLE_RANK[role] >= ROLE_RANK[old_role]):
            message = (f"{user['name']} 的账号已停用，不能加入项目。" if old_role is None
                       else f"{user['name']} 的账号已停用，不能调整权限；可以移除。")
            raise AppError(422, "USER_DISABLED", message, [{"field": "userId", "message": "该账号已停用，不能加入项目。"}])
        if (old_role == "owner" and user["status"] == "active" and role != "owner"
                and _active_owner_count(connection, target, exclude=member_id) == 0):
            raise AppError(409, "LAST_OWNER", LAST_OWNER_MESSAGE, details={"activeOwnerCount": 1})
        if existing is None:
            connection.execute(
                """insert into project_members (project_id, user_id, role, created_at, updated_at, updated_by)
                   values (%s, %s, %s, now(), now(), %s)""",
                (target, member_id, role, actor.user_id),
            )
        else:
            connection.execute(
                """update project_members set role = %s, revision = revision + 1, updated_at = now(), updated_by = %s
                   where project_id = %s and user_id = %s""",
                (role, actor.user_id, target, member_id),
            )
        revision = _bump(connection, target)
        _record(connection, target, actor.user_id, member_id, "add" if existing is None else "change_role",
                old_role, role, revision, request_id)
        cleared = (member_assignments.clear_assignments(connection, target, member_id, actor.user_id)
                   if member_assignments.loses_editing(old_role, role) else member_assignments.empty())
        owner_changed = reassign_primary(connection, target, actor.user_id, revision, request_id)
        data = _result(connection, target, actor, owner_changed=owner_changed, cleared=cleared,
                       extra={"item": _item(connection, target, member_id, actor)})
    return {"data": data, "requestId": request_id}


def _item(connection, project_id: UUID, user_id: UUID, actor) -> dict[str, object] | None:
    rows = _member_rows(connection, project_id, ["m.user_id = %s"], [user_id])
    counts = member_assignments.assignment_counts(connection, project_id)
    return _member_public(rows[0], actor, counts) if rows else None


@router.delete("/projects/{project_id}/members/{user_id}", response_model=None)
def remove_member(project_id: str, user_id: str, request: Request, expectedMembersRevision: str | None = None):
    actor = _write_actor(request)
    target = _uuid(project_id, "projectId")
    member_id = _uuid(user_id, "userId")
    if expectedMembersRevision is None or not expectedMembersRevision.strip().isdigit():
        _require_revision(None)
    expected = int(str(expectedMembersRevision))
    request_id = getattr(request.state, "request_id", None)
    with connect() as connection, write_transaction(connection):
        project = _visible_project(connection, target, actor)
        _require_manager(connection, actor, project)  # U-6：编写人员/只读成员不能自己退出 → 403
        _require_not_archived(project)
        _check_members_revision(connection, project, expected)
        existing = connection.execute(
            """select m.role, u.status from project_members m join users u on u.id = m.user_id
               where m.project_id = %s and m.user_id = %s""",
            (target, member_id),
        ).fetchone()
        if existing is None:
            return Response(status_code=204)  # 幂等：不加版本、不写审计
        old_role = str(existing["role"])
        if old_role == "owner" and existing["status"] == "active" and _active_owner_count(connection, target, exclude=member_id) == 0:
            raise AppError(409, "LAST_OWNER", LAST_OWNER_MESSAGE, details={"activeOwnerCount": 1})
        cleared = member_assignments.clear_assignments(connection, target, member_id, actor.user_id)
        connection.execute("delete from project_members where project_id = %s and user_id = %s", (target, member_id))
        revision = _bump(connection, target)
        _record(connection, target, actor.user_id, member_id, "remove", old_role, None, revision, request_id)
        owner_changed = reassign_primary(connection, target, actor.user_id, revision, request_id)
        removed_self = member_id == actor.user_id
        if removed_self and not _is_admin(actor) and "auditor" not in actor.roles:
            # 自己退出后已看不到项目：只返回版本号与负责人，不再返回名单
            data = {"membersRevision": revision, "owners": owners_of(connection, [target])[str(target)],
                    "ownerId": str(connection.execute("select owner_id from projects where id = %s", (target,)).fetchone()["owner_id"]),
                    "activeOwnerCount": _active_owner_count(connection, target), "ownerChanged": owner_changed,
                    "assignmentsCleared": cleared, "removedSelf": True}
        else:
            data = _result(connection, target, actor, owner_changed=owner_changed, cleared=cleared, extra={"removedSelf": removed_self})
    return {"data": data, "requestId": request_id}


@router.post("/projects/{project_id}/members:batch")
def add_members(project_id: str, request: Request, body: MemberBatchBody) -> dict[str, object]:
    """批量添加（B-11）：整批只校验一次版本、只加一次版本；单项失败放进 failed，不回滚其他项。
    已是成员的账号不改角色（ALREADY_MEMBER），避免批量操作误降负责人。"""
    actor = _write_actor(request)
    target = _uuid(project_id, "projectId")
    role = _assignable_role(body.role)
    expected = _require_revision(body.expectedMembersRevision)
    if not body.userIds:
        raise AppError(422, "VALIDATION_FAILED", "请至少选择一个账号。", [{"field": "userIds", "message": "请至少选择一个账号。"}])
    if len(body.userIds) > BATCH_LIMIT:
        raise AppError(422, "VALIDATION_FAILED", f"一次最多添加 {BATCH_LIMIT} 人。", [{"field": "userIds", "message": f"一次最多添加 {BATCH_LIMIT} 人。"}])
    ids: list[UUID] = []
    for value in body.userIds:
        parsed = _uuid(value, "userIds")
        if parsed not in ids:
            ids.append(parsed)
    request_id = getattr(request.state, "request_id", None)
    with connect() as connection, write_transaction(connection):
        project = _visible_project(connection, target, actor)
        _require_manager(connection, actor, project)
        _require_not_archived(project)
        _check_members_revision(connection, project, expected)
        added: list[str] = []
        failed: list[dict[str, str]] = []
        for member_id in ids:
            user = connection.execute("select id, name, status from users where id = %s", (member_id,)).fetchone()
            if user is None:
                failed.append({"userId": str(member_id), "code": "NOT_FOUND", "message": "没有这个账号。"})
                continue
            if user["status"] != "active":
                failed.append({"userId": str(member_id), "code": "USER_DISABLED", "message": f"{user['name']} 的账号已停用，不能加入项目。"})
                continue
            current = connection.execute(
                "select role from project_members where project_id = %s and user_id = %s", (target, member_id)
            ).fetchone()
            if current is not None:
                failed.append({"userId": str(member_id), "code": "ALREADY_MEMBER",
                               "message": f"{user['name']} 已是{ROLE_TEXT.get(str(current['role']), '成员')}。"})
                continue
            connection.execute(
                """insert into project_members (project_id, user_id, role, created_at, updated_at, updated_by)
                   values (%s, %s, %s, now(), now(), %s)""",
                (target, member_id, role, actor.user_id),
            )
            added.append(str(member_id))
        owner_changed = None
        if added:
            revision = _bump(connection, target)
            for member_id in added:
                _record(connection, target, actor.user_id, UUID(member_id), "add", None, role, revision, request_id)
            owner_changed = reassign_primary(connection, target, actor.user_id, revision, request_id)
        data = _result(connection, target, actor, owner_changed=owner_changed, cleared=member_assignments.empty(),
                       extra={"added": added, "failed": failed})
    return {"data": data, "requestId": request_id}


@router.get("/projects/{project_id}/member-candidates")
def member_candidates(project_id: str, request: Request, q: str = "", page: int = 1, pageSize: int = 50) -> dict[str, object]:
    """候选账号（B-3）：只有本项目负责人和系统管理员能调；只返回启用账号。"""
    actor = require_session(request)
    target = _uuid(project_id, "projectId")
    if page < 1 or not 1 <= pageSize <= CANDIDATE_PAGE_LIMIT or len(q) > 120:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    with connect() as connection:
        project = _visible_project(connection, target, actor)
        _require_manager(connection, actor, project, "查看候选账号")
        filters = ["u.status = 'active'"]
        params: list[object] = []
        if q.strip():
            pattern = _like(q.strip())
            filters.append("(u.name like %s escape '\\' or u.account_key like %s escape '\\' or u.department like %s escape '\\')")
            params.extend([pattern, _like(q.strip().casefold()), pattern])
        where = " and ".join(filters)
        total = int(connection.execute(f"select count(*) as total from users u where {where}", params).fetchone()["total"])
        rows = connection.execute(
            f"""select u.id, u.name, u.account, u.department, m.role
                from users u left join project_members m on m.user_id = u.id and m.project_id = %s
                where {where}
                order by u.name, u.account, u.id
                limit %s offset %s""",
            [target, *params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    items = [{"id": str(row["id"]), "name": row["name"], "account": row["account"], "department": row["department"],
              "isMember": row["role"] is not None, "currentRole": row["role"]} for row in rows]
    return {"data": {"items": items, "total": total, "page": page, "pageSize": pageSize}, "requestId": request.state.request_id}


@router.get("/projects/{project_id}/member-events")
def member_events(project_id: str, request: Request, limit: int = 50, before: str | None = None) -> dict[str, object]:
    """成员变更审计（B-14）：负责人、系统管理员、审计员可看；编写人员和只读成员 403。本期界面不使用。"""
    actor = require_session(request)
    target = _uuid(project_id, "projectId")
    if not 1 <= limit <= 100:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    with connect() as connection:
        project = _visible_project(connection, target, actor)
        if not (_manages(actor, project) or project["role"] == AUDIT_PROJECT_ROLE or "auditor" in actor.roles):
            _require_manager(connection, actor, project, "查看成员调整记录")
        filters, params = ["e.project_id = %s"], [target]
        if before:
            filters.append("e.created_at < %s")
            params.append(before.replace("T", " ").removesuffix("Z"))
        rows = connection.execute(
            f"""select e.id, e.actor_id, a.name as actor_name, e.target_user_id, t.name as target_name,
                       e.action, e.old_role, e.new_role, e.members_revision, e.created_at
                from project_member_events e
                left join users a on a.id = e.actor_id
                left join users t on t.id = e.target_user_id
                where {" and ".join(filters)}
                order by e.created_at desc, e.rowid desc limit %s""",
            [*params, limit],
        ).fetchall()
    items = [{"id": str(row["id"]),
              "actor": None if row["actor_id"] is None else {"id": str(row["actor_id"]), "name": row["actor_name"]},
              "target": {"id": str(row["target_user_id"]), "name": row["target_name"]},
              "action": row["action"], "oldRole": row["old_role"], "newRole": row["new_role"],
              "membersRevision": int(row["members_revision"]), "createdAt": format_timestamp(row["created_at"])}
             for row in rows]
    return {"data": {"items": items}, "requestId": request.state.request_id}


def sole_owner_projects(connection, user_id: UUID) -> list[dict[str, object]]:
    """R2：user_id 是唯一启用负责人的项目（停用该账号会让这些项目没有启用负责人）。按项目名排序。
    归档项目也计入（U-5 字面口径），带 archived 标记，界面提示先恢复项目。"""
    rows = connection.execute(
        """select p.id, p.name, p.archived_at from project_members m
           join projects p on p.id = m.project_id
           where m.user_id = %s and m.role = 'owner'
             and not exists (
               select 1 from project_members o join users u on u.id = o.user_id
               where o.project_id = m.project_id and o.role = 'owner' and o.user_id <> m.user_id and u.status = 'active')
           order by p.name, p.id""",
        (user_id,),
    ).fetchall()
    return [{"id": str(row["id"]), "name": row["name"], "archived": row["archived_at"] is not None} for row in rows]


def reject_disabling_sole_owner(connection, user_id: UUID) -> None:
    projects = sole_owner_projects(connection, user_id)
    if projects:
        raise AppError(409, "LAST_OWNER", f"该账号是 {len(projects)} 个项目唯一的启用负责人，请先在这些项目中把其他成员设为负责人，再停用。",
                       details={"projects": projects[:SOLE_OWNER_LIST_LIMIT], "total": len(projects)})


__all__ = ["router", "owners_of", "reassign_primary", "sole_owner_projects", "reject_disabling_sole_owner",
           "PROJECT_ARCHIVED_MESSAGE", "ROLE_TEXT"]
