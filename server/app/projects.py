"""Projects and members. Visibility is membership, except system administrators."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.document_access import effective_owner_sql
from app.errors import AppError
from app.identity import require_csrf, require_permission, require_session
from app.models import format_timestamp
from app.permissions import has_permission

router = APIRouter()

_STATUSES = {"not_started", "active", "paused", "completed"}
_EFFECTIVE_OWNER = effective_owner_sql("p")
_MY_ROLES = {"owner", "editor", "viewer"}
_SORTS = {
    "updatedAt_desc": "p.updated_at desc, p.name asc",
    "updatedAt_asc": "p.updated_at asc, p.name asc",
    "name_asc": "p.name asc",
}


class ProjectCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    organization: str = ""
    department: str = ""
    group: str = ""
    clientContact: str = ""
    ownerId: str
    periodStart: str | None = None
    periodEnd: str | None = None
    status: str


class ProjectPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    name: str | None = None
    description: str | None = None
    organization: str | None = None
    department: str | None = None
    group: str | None = None
    clientContact: str | None = None
    ownerId: str | None = None
    periodStart: str | None = None
    periodEnd: str | None = None
    status: str | None = None


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    reason: str


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "标识不正确。",
            [{"field": field, "message": "需要有效标识。"}],
        ) from None


def _date(value: str | None, field: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "日期不正确。",
            [{"field": field, "message": "使用 YYYY-MM-DD。"}],
        ) from None


def _period(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise AppError(422, "VALIDATION_FAILED", "结束日期不能早于开始日期。")


def _text(value: str, limit: int, field: str, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "名称不能为空。",
            [{"field": field, "message": "请填写。"}],
        )
    if len(cleaned) > limit:
        raise AppError(422, "VALIDATION_FAILED", "内容超过长度。")
    return cleaned


def _reason(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 1000:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "请填写原因。",
            [{"field": "reason", "message": "原因不能为空。"}],
        )
    return cleaned


def _is_admin(roles: tuple[str, ...]) -> bool:
    return "system_admin" in roles


def _reads_all(roles: tuple[str, ...]) -> bool:
    """测试复测 I6（测试工程师决定，待用户确认）：auditor 是全局只读审计，和系统管理员一样能看到所有项目，但不能写。"""
    return "system_admin" in roles or "auditor" in roles


def _write_actor(request: Request, permission: str, connection, project_id: str):
    """测试复测 I7（待用户确认）：项目写操作先按可见性判断——看不到这个项目一律 404（与读一致，不泄露存在性），
    看得到但权限不足才 403。"""
    actor = require_session(request)
    require_csrf(request, actor)
    _load(connection, _parse_uuid(project_id, "projectId"), actor.user_id, _reads_all(actor.roles))
    if not has_permission(actor.roles, permission):
        raise AppError(403, "FORBIDDEN", "没有执行这个操作的权限。")
    return actor


def project_role(connection, project_id: UUID, user_id: UUID) -> str | None:
    row = connection.execute(
        """
        select role from project_members
        where project_id = %s and user_id = %s
        """,
        (project_id, user_id),
    ).fetchone()
    return None if row is None else str(row["role"])


def require_project_owner(connection, actor, project_id: UUID) -> None:
    if _is_admin(actor.roles):
        return
    if project_role(connection, project_id, actor.user_id) != "owner":
        raise AppError(403, "FORBIDDEN", "只有项目负责人可以修改这个项目。")


def require_project_editor(connection, actor, project_id: UUID) -> None:
    """项目内写入（文档计划、项目文件上传）：编写人员及以上；归档项目只读（A05 P-4）→ 409 PROJECT_ARCHIVED。"""
    from app.document_access import ensure_project_writable

    if not _is_admin(actor.roles) and project_role(connection, project_id, actor.user_id) not in {"owner", "editor"}:
        raise AppError(403, "FORBIDDEN", "只有项目的编写人员可以修改文档。")
    ensure_project_writable(connection, project_id)


def _public(row: dict) -> dict[str, object]:
    archived = row["archived_at"]
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "organization": row["organization"],
        "department": row["department"],
        "group": row["group_name"],
        "clientContact": row["client_contact"],
        # 审查 S-1/S-2：读时计算的有效主负责人（只含启用账号）；没有启用负责人时两者都是 null，界面显示警告
        "ownerId": None if row["owner_id"] is None else str(row["owner_id"]),
        "ownerName": row["owner_name"],
        "periodStart": None if row["period_start"] is None else row["period_start"].isoformat(),
        "periodEnd": None if row["period_end"] is None else row["period_end"].isoformat(),
        "status": row["status"],
        "archivedAt": None if archived is None else format_timestamp(archived),
        "revision": row["revision"],
        # A05 R5：成员名单独立版本号（成员变更不加 revision）
        "membersRevision": row["members_revision"],
        "createdAt": format_timestamp(row["created_at"]),
        "updatedAt": format_timestamp(row["updated_at"]),
        "softwareVersion": row["software_version"],
    }


def _visible_sql(admin: bool) -> str:
    if admin:
        return "true"
    return """
        exists (
          select 1 from project_members m
          where m.project_id = p.id and m.user_id = %s
        )
    """


def _load(connection, project_id: UUID, user_id: UUID, admin: bool) -> dict[str, object]:
    params: list[object] = [project_id]
    visibility = "true"
    if not admin:
        visibility = _visible_sql(False)
        params.append(user_id)
    row = connection.execute(
        f"""
        select p.id, p.name, p.description, p.organization, p.department, p.group_name, p.client_contact,
               {_EFFECTIVE_OWNER} as owner_id, owner.name as owner_name, p.period_start, p.period_end,
               p.status, p.archived_at, p.revision, p.members_revision, p.created_at, p.updated_at,
               p.software_version
        from projects p
        left join users owner on owner.id = {_EFFECTIVE_OWNER}
        where p.id = %s and {visibility}
        """,
        params,
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
    return _public(row)


def _owner_count(connection, project_id: UUID, exclude: UUID | None = None) -> int:
    row = connection.execute(
        """
        select count(*) as total
        from project_members
        where project_id = %s and role = 'owner'
          and (%s::uuid is null or user_id <> %s)
        """,
        (project_id, exclude, exclude),
    ).fetchone()
    return int(row["total"]) if row else 0


def _active_owner(connection, owner_id: UUID) -> None:
    row = connection.execute(
        "select status from users where id = %s",
        (owner_id,),
    ).fetchone()
    if row is None or row["status"] != "active":
        raise AppError(422, "VALIDATION_FAILED", "负责人需要是启用中的账号。")


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _envelope(request: Request, project: dict[str, object]) -> dict[str, object]:
    return {"data": project, "requestId": request.state.request_id}


def _check_revision(current: int, expected: int) -> None:
    if current != expected:
        raise AppError(
            409,
            "REVISION_CONFLICT",
            "这个项目刚被改过，请重新读取后再保存。",
            current_revision=current,
        )


def _block_archive_if_jobs(connection, project_id: UUID) -> None:
    row = connection.execute(
        """
        select count(*) as total
        from jobs
        where project_id = %s and status in
          ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested',
           'waiting_input', 'needs_verification')
        """,
        (project_id,),
    ).fetchone()
    if row and int(row["total"]) > 0:
        raise AppError(409, "INVALID_TRANSITION", "还有未处理的运行任务，暂时不能归档。")


def _member_event(connection, project_id: UUID, actor_id: UUID | None, target: UUID, action: str,
                  old_role: str | None, new_role: str | None, revision: int, request_id: str | None) -> None:
    connection.execute(
        """insert into project_member_events
             (id, project_id, actor_id, target_user_id, action, old_role, new_role, members_revision, request_id)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (uuid4(), project_id, actor_id, target, action, old_role, new_role, revision, request_id),
    )


def _transfer_owner(connection, request: Request, actor, project_id: UUID, previous: UUID, owner_id: UUID) -> None:
    """“编辑项目”里改负责人（旧接口，界面现已改为在“成员与权限”中调整）：新负责人设为 owner 并成为主负责人；
    原主负责人是唯一 owner 时降为编写人员（原行为）。成员变化同样递增 members_revision 并写审计（A05 R5/B-14）。"""
    _active_owner(connection, owner_id)
    request_id = getattr(request.state, "request_id", None)
    connection.execute("update projects set members_revision = members_revision + 1 where id = %s", (project_id,))
    revision = int(connection.execute("select members_revision from projects where id = %s", (project_id,)).fetchone()["members_revision"])
    if _owner_count(connection, project_id, exclude=previous) == 0:
        demoted = connection.execute(
            """
            update project_members
            set role = 'editor', revision = revision + 1, updated_at = now(), updated_by = %s
            where project_id = %s and user_id = %s and role = 'owner'
            """,
            (actor.user_id, project_id, previous),
        ).rowcount
        if demoted:
            _member_event(connection, project_id, actor.user_id, previous, "change_role", "owner", "editor", revision, request_id)
    existing = project_role(connection, project_id, owner_id)
    connection.execute(
        """
        insert into project_members (project_id, user_id, role, created_at, updated_at, updated_by)
        values (%s, %s, 'owner', now(), now(), %s)
        on conflict (project_id, user_id)
        do update set role = 'owner', revision = project_members.revision + 1, updated_at = now(), updated_by = excluded.updated_by
        """,
        (project_id, owner_id, actor.user_id),
    )
    _member_event(connection, project_id, actor.user_id, owner_id, "add" if existing is None else "change_role",
                  existing, "owner", revision, request_id)


@router.get("/projects")
def list_projects(
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    sort: str = "updatedAt_desc",
    name: str = "",
    department: str = "",
    ownerId: str | None = None,
    status: str | None = None,
    archived: bool = False,
    myRole: str | None = None,
) -> dict[str, object]:
    actor = require_session(request)  # S1：按项目成员可见，不再要求全局 projects.read
    if page < 1 or not 1 <= pageSize <= 50 or sort not in _SORTS:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    if status is not None and status not in _STATUSES:
        raise AppError(422, "VALIDATION_FAILED", "项目状态不正确。")
    if myRole is not None and myRole not in _MY_ROLES:
        raise AppError(422, "VALIDATION_FAILED", "项目权限不正确。")
    admin = _reads_all(actor.roles)
    filters = ["p.archived_at is null" if not archived else "p.archived_at is not null"]
    params: list[object] = []
    if not admin:
        filters.append(_visible_sql(False))
        params.append(actor.user_id)
    if name.strip():
        filters.append("p.name ilike %s escape '\\'")
        params.append(_like(name.strip()))
    if department.strip():
        filters.append("p.department ilike %s escape '\\'")
        params.append(_like(department.strip()))
    if ownerId:
        filters.append(f"{_EFFECTIVE_OWNER} = %s")  # 审查 S-2：按有效主负责人筛选（停用的主负责人查不到）
        params.append(_parse_uuid(ownerId, "ownerId"))
    if status:
        filters.append("p.status = %s")
        params.append(status)
    if myRole:
        filters.append("exists (select 1 from project_members mr where mr.project_id = p.id and mr.user_id = %s and mr.role = %s)")
        params.extend([actor.user_id, myRole])
    where = " and ".join(filters)
    with connect() as connection:
        total_row = connection.execute(
            f"select count(*) as total from projects p where {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"""
            select p.id, p.name, p.description, p.organization, p.department, p.group_name, p.client_contact,
                   {_EFFECTIVE_OWNER} as owner_id, owner.name as owner_name, p.period_start, p.period_end,
                   p.status, p.archived_at, p.revision, p.members_revision, p.created_at, p.updated_at,
                   p.software_version, my.role as my_role
            from projects p
            left join users owner on owner.id = {_EFFECTIVE_OWNER}
            left join project_members my on my.project_id = p.id and my.user_id = %s
            where {where}
            order by {_SORTS[sort]}
            limit %s offset %s
            """,
            [actor.user_id, *params, pageSize, (page - 1) * pageSize],
        ).fetchall()
        from app.project_members import owners_of

        owners = owners_of(connection, [row["id"] for row in rows])
    total = int(total_row["total"]) if total_row else 0
    items = []
    for row in rows:
        item = _public(row)
        # A05 B-7：本人在项目中的角色（以管理员/审计员身份可见但不是成员时为 null）；B-6 owners[]
        item["myRole"] = row["my_role"]
        item["owners"] = owners[str(row["id"])]
        items.append(item)
    return {
        "data": {
            "items": items,
            "page": page,
            "pageSize": pageSize,
            "total": total,
        },
        "requestId": request.state.request_id,
    }


@router.get("/project-owners")
def list_project_owners(request: Request, page: int = 1, pageSize: int = 50) -> dict[str, object]:
    actor = require_permission(request, "projects.create")
    if page < 1 or not 1 <= pageSize <= 50:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    filters = "status = 'active'"
    params: list[object] = []
    if not _is_admin(actor.roles):
        filters += " AND id = %s"
        params.append(actor.user_id)
    with connect() as connection:
        total = connection.execute(f"SELECT COUNT(*) AS total FROM users WHERE {filters}", params).fetchone()["total"]
        rows = connection.execute(
            f"SELECT id, name, account FROM users WHERE {filters} ORDER BY name, account, id LIMIT %s OFFSET %s",
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    return {
        "data": {"items": [{"id": str(row["id"]), "name": row["name"], "account": row["account"]} for row in rows],
                 "total": total, "page": page, "pageSize": pageSize},
        "requestId": request.state.request_id,
    }


@router.post("/projects", status_code=201)
def create_project(request: Request, body: ProjectCreateBody) -> dict[str, object]:
    actor = require_permission(request, "projects.create")
    key = request.headers.get("idempotency-key", "").strip()
    if not key or len(key) > 128:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "缺少本次请求的稳定键。",
            [{"field": "Idempotency-Key", "message": "需要填写。"}],
        )
    if body.status not in _STATUSES:
        raise AppError(422, "VALIDATION_FAILED", "项目状态不正确。")
    project_name = _text(body.name, 120, "name", required=True)
    description = _text(body.description, 2000, "description")
    organization = _text(body.organization, 120, "organization")
    department = _text(body.department, 120, "department")
    group_name = _text(body.group, 120, "group")
    client_contact = _text(body.clientContact, 120, "clientContact")
    owner_id = _parse_uuid(body.ownerId, "ownerId")
    if not _is_admin(actor.roles) and owner_id != actor.user_id:
        raise AppError(403, "FORBIDDEN", "请将自己设置为项目负责人。")
    start = _date(body.periodStart, "periodStart")
    end = _date(body.periodEnd, "periodEnd")
    _period(start, end)
    payload = json.dumps(
        {
            "name": project_name,
            "ownerId": str(owner_id),
            "status": body.status,
            "organization": organization,
            "department": department,
            "group": group_name,
            "clientContact": client_contact,
            "description": description,
            "periodStart": None if start is None else start.isoformat(),
            "periodEnd": None if end is None else end.isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            select request_hash, result_project_id
            from idempotency_keys
            where user_id = %s and idempotency_key = %s
            """,
            (actor.user_id, key),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash or existing["result_project_id"] is None:
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一个请求键不能改成另一份内容。")
            return _envelope(
                request,
                _load(connection, existing["result_project_id"], actor.user_id, _is_admin(actor.roles)),
            )
        _active_owner(connection, owner_id)
        project_id = uuid4()
        with connection.transaction():
            connection.execute(
                """
                insert into projects (
                  id, name, description, organization, department, group_name, client_contact,
                  owner_id, period_start, period_end, status
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    project_name,
                    description,
                    organization,
                    department,
                    group_name,
                    client_contact,
                    owner_id,
                    start,
                    end,
                    body.status,
                ),
            )
            connection.execute(
                """
                insert into project_members (project_id, user_id, role, created_at, updated_at, updated_by)
                values (%s, %s, 'owner', now(), now(), %s)
                """,
                (project_id, owner_id, actor.user_id),
            )
            _member_event(connection, project_id, actor.user_id, owner_id, "add", None, "owner", 1,
                          getattr(request.state, "request_id", None))
            connection.execute(
                """
                insert into idempotency_keys (
                  user_id, idempotency_key, request_hash, result_project_id
                )
                values (%s, %s, %s, %s)
                """,
                (actor.user_id, key, request_hash, project_id),
            )
            created = _load(connection, project_id, actor.user_id, True)
        connection.commit()
    return _envelope(request, created)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, object]:
    actor = require_session(request)  # S1：按项目成员可见，不再要求全局 projects.read
    target = _parse_uuid(project_id, "projectId")
    with connect() as connection:
        admin = _is_admin(actor.roles)
        project = _load(connection, target, actor.user_id, _reads_all(actor.roles))
        from app.document_access import member_role

        # 三审：非成员的全局审计员返回 "auditor"（与文档接口 permissions.projectRole 口径一致），不是 null
        role = member_role(connection, target, actor.user_id)
        from app.project_members import owners_of

        project["owners"] = owners_of(connection, [target])[str(target)]
    archived = project["archivedAt"] is not None
    # 测试 L2：前端按此隐藏写操作（与 update_project / 成员管理的判定一致）。
    # A05 R3：归档项目只读（canEdit/canManageMembers=false，readonlyReason='archived'），可查看和导出。
    manages = admin or role == "owner"
    can_edit = has_permission(actor.roles, "projects.manage") and manages and not archived
    project["permissions"] = {
        "projectRole": role,
        "canEdit": can_edit,
        "canManageMembers": manages and not archived,
        "readonlyReason": "archived" if archived else (None if manages else "role"),
    }
    return _envelope(request, project)


@router.patch("/projects/{project_id}")
def update_project(project_id: str, request: Request, body: ProjectPatchBody) -> dict[str, object]:
    with connect() as connection:
        actor = _write_actor(request, "projects.manage", connection, project_id)
    target = _parse_uuid(project_id, "projectId")
    admin = _is_admin(actor.roles)
    with connect() as connection:
        with connection.transaction(immediate=True):
            current = _load(connection, target, actor.user_id, admin)
            require_project_owner(connection, actor, target)
            if current["archivedAt"] is not None:
                # A05 R3 / P-4：归档项目只读，恢复后才能修改（恢复走 /unarchive）
                raise AppError(409, "PROJECT_ARCHIVED", "项目已归档，恢复后才能修改。", details={"readonlyReason": "archived"})
            _check_revision(int(current["revision"]), body.expectedRevision)
            name = current["name"] if body.name is None else _text(body.name, 120, "name", True)
            description = current["description"] if body.description is None else _text(body.description, 2000, "description")
            organization = current["organization"] if body.organization is None else _text(body.organization, 120, "organization")
            department = current["department"] if body.department is None else _text(body.department, 120, "department")
            group_name = current["group"] if body.group is None else _text(body.group, 120, "group")
            client_contact = current["clientContact"] if body.clientContact is None else _text(body.clientContact, 120, "clientContact")
            status = current["status"] if body.status is None else body.status
            if status not in _STATUSES:
                raise AppError(422, "VALIDATION_FAILED", "项目状态不正确。")
            # 存储的 owner_id（不是读时计算的有效主负责人）：改资料不能顺手把主负责人“懒改指”掉（审查 S-4）
            stored_owner = connection.execute("select owner_id from projects where id = %s", (target,)).fetchone()["owner_id"]
            owner_id = stored_owner if body.ownerId is None else _parse_uuid(body.ownerId, "ownerId")
            if body.periodStart is None and "periodStart" not in body.model_fields_set:
                start = None if current["periodStart"] is None else date.fromisoformat(str(current["periodStart"]))
            else:
                start = _date(body.periodStart, "periodStart")
            if body.periodEnd is None and "periodEnd" not in body.model_fields_set:
                end = None if current["periodEnd"] is None else date.fromisoformat(str(current["periodEnd"]))
            else:
                end = _date(body.periodEnd, "periodEnd")
            _period(start, end)
            if body.ownerId is not None and str(owner_id) != str(stored_owner):
                _transfer_owner(connection, request, actor, target, stored_owner, owner_id)
            connection.execute(
                """
                update projects
                set name = %s, description = %s, organization = %s, department = %s,
                    group_name = %s, client_contact = %s, owner_id = %s, period_start = %s, period_end = %s,
                    status = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (
                    name,
                    description,
                    organization,
                    department,
                    group_name,
                    client_contact,
                    owner_id,
                    start,
                    end,
                    status,
                    target,
                ),
            )
            updated = _load(connection, target, actor.user_id, admin)
    return _envelope(request, updated)


@router.post("/projects/{project_id}/archive")
def archive_project(project_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    with connect() as connection:
        actor = _write_actor(request, "projects.manage", connection, project_id)
    target = _parse_uuid(project_id, "projectId")
    _reason(body.reason)
    admin = _is_admin(actor.roles)
    with connect() as connection:
        with connection.transaction(immediate=True):
            current = _load(connection, target, actor.user_id, admin)
            require_project_owner(connection, actor, target)
            _check_revision(int(current["revision"]), body.expectedRevision)
            if current["archivedAt"] is None:
                _block_archive_if_jobs(connection, target)
                connection.execute(
                    """
                    update projects
                    set archived_at = now(), revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
            updated = _load(connection, target, actor.user_id, admin)
    return _envelope(request, updated)


@router.post("/projects/{project_id}/unarchive")
def unarchive_project(project_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    with connect() as connection:
        actor = _write_actor(request, "projects.manage", connection, project_id)
    target = _parse_uuid(project_id, "projectId")
    _reason(body.reason)
    admin = _is_admin(actor.roles)
    with connect() as connection:
        with connection.transaction(immediate=True):
            current = _load(connection, target, actor.user_id, admin)
            require_project_owner(connection, actor, target)
            _check_revision(int(current["revision"]), body.expectedRevision)
            connection.execute(
                """
                update projects
                set archived_at = null, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (target,),
            )
            updated = _load(connection, target, actor.user_id, admin)
    return _envelope(request, updated)
