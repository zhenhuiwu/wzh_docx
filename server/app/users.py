"""Account administration. Passwords never appear in list or user payloads."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.identity import (
    account_key,
    create_user,
    require_permission,
)
from app.models import format_timestamp
from app.permissions import GLOBAL_ROLES, permissions_for, role_definitions

router = APIRouter()

_SORTS = {
    "updatedAt_desc": "u.updated_at desc, u.account asc",
    "updatedAt_asc": "u.updated_at asc, u.account asc",
    "name_asc": "u.name asc, u.account asc",
}


class UserCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    name: str
    department: str = ""
    # 省略或空数组：不授予全局角色，即“仅项目成员”账号（只能访问被加入的项目）。
    globalRoles: list[str] = Field(default_factory=list, max_length=10)
    password: str


class UserPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    name: str | None = None
    department: str | None = None


class UserRolesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    globalRoles: list[str]
    reason: str


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    reason: str


class ResetCredentialsBody(ReasonBody):
    password: str


def _public(row: dict, roles: list[str]) -> dict[str, object]:
    last_login = row["last_login_at"]
    return {
        "id": str(row["id"]),
        "account": row["account"],
        "name": row["name"],
        "department": row["department"],
        "globalRoles": roles,
        "status": row["status"],
        "revision": row["revision"],
        "createdAt": format_timestamp(row["created_at"]),
        "updatedAt": format_timestamp(row["updated_at"]),
        "lastLoginAt": None if last_login is None else format_timestamp(last_login),
    }


def _roles_for(connection, user_id: UUID) -> list[str]:
    rows = connection.execute(
        "select role from user_roles where user_id = %s order by role",
        (user_id,),
    ).fetchall()
    return [row["role"] for row in rows]


def _load(connection, user_id: UUID) -> dict[str, object]:
    row = connection.execute(
        """
        select id, account, name, department, status, revision,
               created_at, updated_at, last_login_at
        from users
        where id = %s
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个账号。")
    return _public(row, _roles_for(connection, user_id))


def _parse_user_id(user_id: str) -> UUID:
    try:
        return UUID(user_id)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "账号标识不正确。",
            [{"field": "userId", "message": "需要有效标识。"}],
        ) from None


def _active_admin_count(connection, exclude: UUID | None = None) -> int:
    row = connection.execute(
        """
        select count(distinct u.id) as total
        from users u
        join user_roles r on r.user_id = u.id
        where u.status = 'active'
          and r.role = 'system_admin'
          and (%s::uuid is null or u.id <> %s)
        """,
        (exclude, exclude),
    ).fetchone()
    return int(row["total"]) if row else 0


def _reject_last_admin(connection, user_id: UUID, next_roles: list[str] | None, disabling: bool) -> None:
    roles = _roles_for(connection, user_id) if next_roles is None else next_roles
    row = connection.execute(
        "select status from users where id = %s",
        (user_id,),
    ).fetchone()
    if row is None or row["status"] != "active":
        return
    still_admin = "system_admin" in roles and not disabling
    if still_admin:
        return
    if "system_admin" not in _roles_for(connection, user_id):
        return
    if _active_admin_count(connection, exclude=user_id) == 0:
        raise AppError(409, "INVALID_TRANSITION", "不能停用或取消最后一个管理员。")


def _check_revision(row_revision: int, expected: int) -> None:
    if row_revision != expected:
        raise AppError(
            409,
            "REVISION_CONFLICT",
            "这个账号刚被改过，请重新读取后再保存。",
            current_revision=row_revision,
        )


def _clean_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > 1000:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "请填写原因。",
            [{"field": "reason", "message": "原因不能为空。"}],
        )
    return cleaned


_MAX_ROLES = 10


def _grantable(actor_roles: tuple[str, ...] | list[str], requested: list[str]) -> list[str]:
    """校验要授予的全局角色；空列表合法（不授予全局角色）。"""
    if len(requested) > _MAX_ROLES:
        raise AppError(422, "VALIDATION_FAILED", "角色不正确。")
    unknown = [role for role in requested if role not in GLOBAL_ROLES]
    if unknown:
        raise AppError(422, "VALIDATION_FAILED", "角色不在可分配范围内。")
    roles = list(dict.fromkeys(requested))
    # 系统管理员只能由系统管理员授予（即使将来有别的角色拿到 users.manage）。
    if "system_admin" in roles and "system_admin" not in actor_roles:
        raise AppError(403, "FORBIDDEN", "只有系统管理员能授予系统管理员角色。")
    actor_permissions = set(permissions_for(actor_roles))
    for role in roles:
        needed = set(permissions_for([role]))
        if not needed.issubset(actor_permissions):
            raise AppError(403, "FORBIDDEN", "不能分配超出自己权限的角色。")
    return roles


def _roles_text(roles: list[str]) -> str:
    return "、".join(roles) if roles else "无（仅项目成员）"


def _record(connection, user_id: UUID, actor_id: UUID, action: str, reason: str) -> None:
    connection.execute(
        """
        insert into user_admin_events (id, user_id, actor_id, action, reason)
        values (%s, %s, %s, %s, %s)
        """,
        (uuid4(), user_id, actor_id, action, reason),
    )


def _revoke(connection, user_id: UUID, keep_session: UUID | None = None) -> None:
    if keep_session is None:
        connection.execute(
            """
            update sessions
            set revoked_at = now()
            where user_id = %s and revoked_at is null
            """,
            (user_id,),
        )
        return
    connection.execute(
        """
        update sessions
        set revoked_at = now()
        where user_id = %s and revoked_at is null and id <> %s
        """,
        (user_id, keep_session),
    )


def _mark_jobs(connection, user_id: UUID) -> None:
    connection.execute(
        """
        update jobs
        set status = 'needs_verification', revision = revision + 1, updated_at = now()
        where created_by = %s
          and status in ('queued', 'running', 'pause_requested', 'paused',
                         'cancel_requested', 'waiting_input')
        """,
        (user_id,),
    )


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _envelope(request: Request, user: dict[str, object]) -> dict[str, object]:
    return {"data": user, "requestId": request.state.request_id}


@router.get("/users")
def list_users(
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    sort: str = "updatedAt_desc",
    name: str = "",
    account: str = "",
    department: str = "",
    status: str | None = None,
    role: str | None = None,
) -> dict[str, object]:
    require_permission(request, "users.read")
    if page < 1 or not 1 <= pageSize <= 50 or sort not in _SORTS:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    if status not in {None, "active", "disabled"}:
        raise AppError(422, "VALIDATION_FAILED", "启停状态不正确。")
    if role is not None and role not in GLOBAL_ROLES:
        raise AppError(422, "VALIDATION_FAILED", "角色不正确。")
    filters = ["true"]
    params: list[object] = []
    if name.strip():
        filters.append("u.name ilike %s escape '\\'")
        params.append(_like(name.strip()))
    if account.strip():
        filters.append("u.account_key like %s escape '\\'")
        params.append(_like(account_key(account)))
    if department.strip():
        filters.append("u.department ilike %s escape '\\'")
        params.append(_like(department.strip()))
    if status:
        filters.append("u.status = %s")
        params.append(status)
    if role:
        filters.append(
            "exists (select 1 from user_roles r where r.user_id = u.id and r.role = %s)"
        )
        params.append(role)
    where = " and ".join(filters)
    with connect() as connection:
        total_row = connection.execute(
            f"select count(*) as total from users u where {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"""
            select u.id, u.account, u.name, u.department, u.status, u.revision,
                   u.created_at, u.updated_at, u.last_login_at
            from users u
            where {where}
            order by {_SORTS[sort]}
            limit %s offset %s
            """,
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
        items = [_public(row, _roles_for(connection, row["id"])) for row in rows]
    total = int(total_row["total"]) if total_row else 0
    return {
        "data": {"items": items, "page": page, "pageSize": pageSize, "total": total},
        "requestId": request.state.request_id,
    }


@router.post("/users", status_code=201)
def create_account(request: Request, body: UserCreateBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    key = request.headers.get("idempotency-key", "").strip()
    if not key or len(key) > 128:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "缺少本次请求的稳定键。",
            [{"field": "Idempotency-Key", "message": "需要填写。"}],
        )
    roles = _grantable(actor.roles, body.globalRoles)
    payload = json.dumps(
        {
            "account": body.account.strip(),
            "name": body.name.strip(),
            "department": body.department.strip(),
            "globalRoles": roles,
            "passwordFingerprint": hashlib.sha256(body.password.encode("utf-8")).hexdigest(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with connect() as connection:
        with connection.transaction(immediate=True):
            existing = connection.execute(
                """
                select request_hash, result_user_id
                from idempotency_keys
                where user_id = %s and idempotency_key = %s
                """,
                (actor.user_id, key),
            )
            existing = existing.fetchone()
            if existing:
                if existing["request_hash"] != request_hash or existing["result_user_id"] is None:
                    raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一个请求键不能改成另一份内容。")
                return _envelope(request, _load(connection, existing["result_user_id"]))
            user_id = create_user(
                account=body.account,
                name=body.name,
                password=body.password,
                roles=roles,
                department=body.department.strip(),
                connection=connection,
                must_change_password=True,
                allow_no_roles=True,
            )
            connection.execute(
                """
                insert into idempotency_keys (
                  user_id, idempotency_key, request_hash, result_user_id
                )
                values (%s, %s, %s, %s)
                """,
                (actor.user_id, key, request_hash, user_id),
            )
            _record(connection, user_id, actor.user_id, "create", f"创建账号；全局角色：{_roles_text(roles)}")
            if "system_admin" in roles:
                _record(connection, user_id, actor.user_id, "users.role.grant_admin", "授予系统管理员")
            created = _load(connection, user_id)
    return _envelope(request, created)


@router.patch("/users/{user_id}")
def update_user(user_id: str, request: Request, body: UserPatchBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select revision, name, department from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _check_revision(row["revision"], body.expectedRevision)
            name = row["name"] if body.name is None else body.name.strip()
            department = row["department"] if body.department is None else body.department
            if not name or len(name) > 120 or len(department) > 120:
                raise AppError(422, "VALIDATION_FAILED", "姓名或部门不正确。")
            connection.execute(
                """
                update users
                set name = %s, department = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (name, department, target),
            )
            _record(connection, target, actor.user_id, "update", "修改资料")
            updated = _load(connection, target)
    return _envelope(request, updated)


@router.put("/users/{user_id}/roles")
def set_roles(user_id: str, request: Request, body: UserRolesBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    roles = _grantable(actor.roles, body.globalRoles)
    reason = _clean_reason(body.reason)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select revision from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _check_revision(row["revision"], body.expectedRevision)
            _reject_last_admin(connection, target, roles, disabling=False)
            previous = _roles_for(connection, target)
            connection.execute("delete from user_roles where user_id = %s", (target,))
            for role in roles:
                connection.execute(
                    "insert into user_roles (user_id, role) values (%s, %s)",
                    (target, role),
                )
            connection.execute(
                """
                update users
                set revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (target,),
            )
            _record(connection, target, actor.user_id, "roles", f"{reason}（全局角色：{_roles_text(roles)}）"[:1000])
            if "system_admin" in roles and "system_admin" not in previous:
                _record(connection, target, actor.user_id, "users.role.grant_admin", "授予系统管理员")
            if sorted(previous) != sorted(roles):
                # 权限变了：撤销该用户的会话，让其重新登录后拿到新权限（自己改自己时保留当前会话）。
                keep = actor.session_id if target == actor.user_id else None
                _revoke(connection, target, keep)
            updated = _load(connection, target)
    return _envelope(request, updated)


@router.post("/users/{user_id}/disable")
def disable_user(user_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    reason = _clean_reason(body.reason)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select revision, status from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _check_revision(row["revision"], body.expectedRevision)
            if row["status"] == "active":
                _reject_last_admin(connection, target, [], disabling=True)
                connection.execute(
                    """
                    update users
                    set status = 'disabled', revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
                _revoke(connection, target)
                _mark_jobs(connection, target)
            _record(connection, target, actor.user_id, "disable", reason)
            updated = _load(connection, target)
    return _envelope(request, updated)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    reason = _clean_reason(body.reason)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select revision from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _check_revision(row["revision"], body.expectedRevision)
            connection.execute(
                """
                update users
                set status = 'active', revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (target,),
            )
            _record(connection, target, actor.user_id, "enable", reason)
            updated = _load(connection, target)
    return _envelope(request, updated)


@router.post("/users/{user_id}/reset-credentials")
def reset_credentials(user_id: str, request: Request, body: ResetCredentialsBody) -> dict[str, object]:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    reason = _clean_reason(body.reason)
    from app.identity import _require_new_password, hash_password

    _require_new_password(body.password, "password")
    stored = hash_password(body.password)
    # A password chosen by someone else must be replaced by its owner.
    must_change = target != actor.user_id
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select revision from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _check_revision(row["revision"], body.expectedRevision)
            connection.execute(
                """
                update users
                set password_hash = %s,
                    must_change_password = %s,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (stored, 1 if must_change else 0, target),
            )
            _revoke(connection, target)
            _record(connection, target, actor.user_id, "reset", reason)
    return {
        "data": {
            "userId": str(target),
            "status": "updated",
            "requiresFirstLoginChange": must_change,
            "message": "密码已重置。",
        },
        "requestId": request.state.request_id,
    }


@router.delete("/users/{user_id}/sessions", status_code=204)
def revoke_sessions(user_id: str, request: Request) -> Response:
    actor = require_permission(request, "users.manage")
    target = _parse_user_id(user_id)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = connection.execute(
                "select id from users where id = %s",
                (target,),
            ).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "没有这个账号。")
            _revoke(connection, target)
            _record(connection, target, actor.user_id, "revoke", "撤销会话")
    return Response(status_code=204)


@router.get("/roles")
def list_roles(request: Request) -> dict[str, object]:
    require_permission(request, "users.read")
    return {
        "data": {"items": role_definitions()},
        "requestId": request.state.request_id,
    }
