"""Knowledge bases and source versions. Upload does not make a file searchable."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.identity import require_permission
from app.models import format_timestamp

router = APIRouter()


class KnowledgeCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    summary: str = ""
    scope: str
    projectId: str | None = None
    ownerId: str


class KnowledgePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    name: str | None = None
    summary: str | None = None


class AccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    readers: list[str]
    maintainers: list[str]


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    reason: str


class SourceCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fileId: str


class SourceVersionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fileId: str


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(422, "VALIDATION_FAILED", "标识不正确。", [{"field": field, "message": "需要有效标识。"}])


def _admin(roles: tuple[str, ...]) -> bool:
    return "system_admin" in roles


def _can_see(connection, base_id: UUID, user_id: UUID, admin: bool) -> dict | None:
    if admin:
        return connection.execute("select * from knowledge_bases where id = %s", (base_id,)).fetchone()
    return connection.execute(
        """
        select b.*
        from knowledge_bases b
        where b.id = %s
          and (
            b.owner_id = %s
            or exists (
              select 1 from knowledge_access a
              where a.base_id = b.id and a.user_id = %s
            )
          )
        """,
        (base_id, user_id, user_id),
    ).fetchone()


def _require_base(connection, base_id: UUID, user_id: UUID, admin: bool) -> dict:
    row = _can_see(connection, base_id, user_id, admin)
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个知识库。")
    return row


def _require_writer(connection, base_id: UUID, user_id: UUID, admin: bool) -> dict:
    if admin:
        return _writable(connection, _require_base(connection, base_id, user_id, True))
    row = connection.execute("select * from knowledge_bases where id = %s", (base_id,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个知识库。")
    if row["owner_id"] == user_id:
        return _writable(connection, row)
    access = connection.execute(
        "select role from knowledge_access where base_id = %s and user_id = %s",
        (base_id, user_id),
    ).fetchone()
    if access is not None and access["role"] == "maintainer":
        return _writable(connection, row)
    if access is not None:
        raise AppError(403, "FORBIDDEN", "当前授权只能阅读这个知识库。")
    raise AppError(404, "NOT_FOUND", "没有这个知识库。")


def _writable(connection, row: dict) -> dict:
    """A05 C-1（2026-10-11）：归档项目完全只读——所属项目已归档的项目范围知识库，
    改资料、改授权、归档、登记资料/新版本、上传文件一律 409 PROJECT_ARCHIVED（在可见性与权限检查之后）。
    单位知识库（scope='organization'）不受影响。"""
    if row["scope"] == "project" and row["project_id"] is not None:
        from app.document_access import ensure_project_writable

        ensure_project_writable(connection, row["project_id"])
    return row


def _public_base(row: dict) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "summary": row["summary"],
        "scope": row["scope"],
        "projectId": None if row["project_id"] is None else str(row["project_id"]),
        "ownerId": str(row["owner_id"]),
        "state": row["state"],
        "revision": row["revision"],
        "updatedAt": format_timestamp(row["updated_at"]),
    }


def _public_version(row: dict) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "sourceId": str(row["source_id"]),
        "baseId": str(row["base_id"]),
        "name": row["name"],
        "version": str(row["version_number"]),
        "fileId": str(row["file_id"]),
        "status": row["status"],
        "generation": 0,
        "revision": row["revision"],
        "message": row["message"],
        "updatedAt": format_timestamp(row["updated_at"]),
        "sourceRevision": row["source_revision"],
        "mediaType": row["media_type"],
    }


@router.get("/knowledge-bases")
def list_bases(request: Request, page: int = 1, pageSize: int = 10, name: str = "") -> dict[str, object]:
    actor = require_permission(request, "knowledge.read")
    if page < 1 or not 1 <= pageSize <= 50:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    admin = _admin(actor.roles)
    filters = ["true"]
    params: list[object] = []
    if not admin:
        filters.append("(b.owner_id = %s or exists (select 1 from knowledge_access a where a.base_id = b.id and a.user_id = %s))")
        params.extend([actor.user_id, actor.user_id])
    if name.strip():
        filters.append("b.name ilike %s")
        params.append(f"%{name.strip()}%")
    where = " and ".join(filters)
    with connect() as connection:
        total = connection.execute(f"select count(*) as total from knowledge_bases b where {where}", params).fetchone()
        rows = connection.execute(
            f"""
            select * from knowledge_bases b
            where {where}
            order by b.updated_at desc
            limit %s offset %s
            """,
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    return {
        "data": {"items": [_public_base(row) for row in rows], "page": page, "pageSize": pageSize, "total": int(total["total"]) if total else 0},
        "requestId": request.state.request_id,
    }


@router.post("/knowledge-bases", status_code=201)
def create_base(request: Request, body: KnowledgeCreateBody) -> dict[str, object]:
    require_permission(request, "knowledge.manage")
    name = body.name.strip()
    if not name or body.scope not in {"organization", "project"}:
        raise AppError(422, "VALIDATION_FAILED", "请填写名称和归属。")
    if body.scope == "project" and not body.projectId:
        raise AppError(422, "VALIDATION_FAILED", "项目知识库必须选择项目。", [{"field": "projectId", "message": "请选择项目。"}])
    if body.scope == "organization" and body.projectId:
        raise AppError(422, "VALIDATION_FAILED", "单位知识库不能绑定项目。")
    owner_id = _uuid(body.ownerId, "ownerId")
    project_id = _uuid(body.projectId, "projectId") if body.projectId else None
    base_id = uuid4()
    with connect() as connection:
        with connection.transaction():
            owner = connection.execute("select status from users where id = %s", (owner_id,)).fetchone()
            if owner is None or owner["status"] != "active":
                raise AppError(422, "VALIDATION_FAILED", "维护人需要是启用中的账号。")
            if project_id is not None:
                project = connection.execute("select id, archived_at from projects where id = %s", (project_id,)).fetchone()
                if project is None:
                    raise AppError(422, "VALIDATION_FAILED", "找不到这个项目。")
                if project["archived_at"] is not None:
                    # A05 C-1：归档项目不能新建项目范围知识库
                    from app.document_access import PROJECT_ARCHIVED_MESSAGE

                    raise AppError(409, "PROJECT_ARCHIVED", PROJECT_ARCHIVED_MESSAGE, details={"readonlyReason": "archived"})
            connection.execute(
                """
                insert into knowledge_bases (id, name, summary, scope, project_id, owner_id, state)
                values (%s, %s, %s, %s, %s, %s, 'active')
                """,
                (base_id, name, body.summary.strip(), body.scope, project_id, owner_id),
            )
            created = connection.execute("select * from knowledge_bases where id = %s", (base_id,)).fetchone()
    return {"data": _public_base(created), "requestId": request.state.request_id}


@router.get("/knowledge-bases/{base_id}")
def get_base(base_id: str, request: Request) -> dict[str, object]:
    actor = require_permission(request, "knowledge.read")
    target = _uuid(base_id, "baseId")
    with connect() as connection:
        row = _require_base(connection, target, actor.user_id, _admin(actor.roles))
    return {"data": _public_base(row), "requestId": request.state.request_id}


@router.patch("/knowledge-bases/{base_id}")
def update_base(base_id: str, request: Request, body: KnowledgePatchBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.manage")
    target = _uuid(base_id, "baseId")
    with connect() as connection:
        with connection.transaction():
            row = _require_writer(connection, target, actor.user_id, _admin(actor.roles))
            if int(row["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个知识库刚被改过。", current_revision=int(row["revision"]))
            name = row["name"] if body.name is None else body.name.strip()
            summary = row["summary"] if body.summary is None else body.summary.strip()
            connection.execute(
                """
                update knowledge_bases
                set name = %s, summary = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (name, summary, target),
            )
            updated = connection.execute("select * from knowledge_bases where id = %s", (target,)).fetchone()
    return {"data": _public_base(updated), "requestId": request.state.request_id}


@router.put("/knowledge-bases/{base_id}/access")
def set_access(base_id: str, request: Request, body: AccessBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.access.manage")
    target = _uuid(base_id, "baseId")
    readers = [_uuid(item, "readers") for item in body.readers]
    maintainers = [_uuid(item, "maintainers") for item in body.maintainers]
    with connect() as connection:
        with connection.transaction():
            row = _require_writer(connection, target, actor.user_id, _admin(actor.roles))
            if int(row["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个知识库刚被改过。", current_revision=int(row["revision"]))
            connection.execute("delete from knowledge_access where base_id = %s", (target,))
            for user_id in readers:
                connection.execute(
                    "insert into knowledge_access (base_id, user_id, role) values (%s, %s, 'reader')",
                    (target, user_id),
                )
            for user_id in maintainers:
                connection.execute(
                    """
                    insert into knowledge_access (base_id, user_id, role)
                    values (%s, %s, 'maintainer')
                    on conflict (base_id, user_id) do update set role = 'maintainer'
                    """,
                    (target, user_id),
                )
            connection.execute(
                "update knowledge_bases set revision = revision + 1, updated_at = now() where id = %s",
                (target,),
            )
            updated = connection.execute("select * from knowledge_bases where id = %s", (target,)).fetchone()
    return {"data": _public_base(updated), "requestId": request.state.request_id}


@router.post("/knowledge-bases/{base_id}/archive")
def archive_base(base_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.manage")
    target = _uuid(base_id, "baseId")
    if not body.reason.strip():
        raise AppError(422, "VALIDATION_FAILED", "请填写原因。")
    with connect() as connection:
        with connection.transaction():
            row = _require_writer(connection, target, actor.user_id, _admin(actor.roles))
            if int(row["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个知识库刚被改过。", current_revision=int(row["revision"]))
            connection.execute(
                """
                update knowledge_bases
                set state = 'archived', revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (target,),
            )
            updated = connection.execute("select * from knowledge_bases where id = %s", (target,)).fetchone()
    return {"data": _public_base(updated), "requestId": request.state.request_id}


def _version_join(connection, version_id: UUID, user_id: UUID, admin: bool) -> dict:
    row = connection.execute(
        """
        select v.*, s.name, s.base_id, s.revision as source_revision, s.state as source_state
        from source_versions v
        join sources s on s.id = v.source_id
        where v.id = %s
        """,
        (version_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这份资料。")
    _require_base(connection, row["base_id"], user_id, admin)
    return row


@router.get("/knowledge-bases/{base_id}/sources")
def list_sources(base_id: str, request: Request) -> dict[str, object]:
    actor = require_permission(request, "knowledge.read")
    target = _uuid(base_id, "baseId")
    with connect() as connection:
        _require_base(connection, target, actor.user_id, _admin(actor.roles))
        rows = connection.execute(
            """
            select v.*, s.name, s.base_id, s.revision as source_revision
            from source_versions v
            join sources s on s.id = v.source_id
            where s.base_id = %s
            order by s.name, v.version_number
            """,
            (target,),
        ).fetchall()
    return {"data": {"items": [_public_version(row) for row in rows]}, "requestId": request.state.request_id}


@router.post("/knowledge-bases/{base_id}/sources", status_code=201)
def create_source(base_id: str, request: Request, body: SourceCreateBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.manage")
    target = _uuid(base_id, "baseId")
    file_id = _uuid(body.fileId, "fileId")
    name = body.name.strip()
    if not name:
        raise AppError(422, "VALIDATION_FAILED", "请填写资料名称。")
    with connect() as connection:
        with connection.transaction():
            _require_writer(connection, target, actor.user_id, _admin(actor.roles))
            existing = connection.execute(
                "select id from sources where base_id = %s and name = %s",
                (target, name),
            ).fetchone()
            if existing:
                raise AppError(409, "DUPLICATE_NAME", "同名资料已存在。请登记新版本，不会覆盖旧版本。")
            file_row = connection.execute("select id, media_type from files where id = %s", (file_id,)).fetchone()
            if file_row is None:
                raise AppError(422, "VALIDATION_FAILED", "找不到上传的文件。")
            source_id = uuid4()
            version_id = uuid4()
            connection.execute(
                "insert into sources (id, base_id, name, state) values (%s, %s, %s, 'active')",
                (source_id, target, name),
            )
            connection.execute(
                """
                insert into source_versions (
                  id, source_id, version_number, file_id, media_type, status, message
                )
                values (%s, %s, 1, %s, %s, 'uploaded', %s)
                """,
                (version_id, source_id, file_id, file_row["media_type"], "已上传，尚未解析，不能检索。"),
            )
            created = connection.execute(
                """
                select v.*, s.name, s.base_id, s.revision as source_revision
                from source_versions v join sources s on s.id = v.source_id
                where v.id = %s
                """,
                (version_id,),
            ).fetchone()
    return {"data": _public_version(created), "requestId": request.state.request_id}


@router.get("/source-versions/{source_version_id}")
def get_source_version(source_version_id: str, request: Request) -> dict[str, object]:
    actor = require_permission(request, "knowledge.read")
    target = _uuid(source_version_id, "sourceVersionId")
    with connect() as connection:
        row = _version_join(connection, target, actor.user_id, _admin(actor.roles))
    return {"data": _public_version(row), "requestId": request.state.request_id}


@router.post("/sources/{source_id}/versions", status_code=201)
def create_source_version(source_id: str, request: Request, body: SourceVersionCreateBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.manage")
    target = _uuid(source_id, "sourceId")
    file_id = _uuid(body.fileId, "fileId")
    with connect() as connection:
        with connection.transaction():
            source = connection.execute("select * from sources where id = %s", (target,)).fetchone()
            if source is None:
                raise AppError(404, "NOT_FOUND", "没有这份资料。")
            _require_writer(connection, source["base_id"], actor.user_id, _admin(actor.roles))
            duplicate = connection.execute(
                "select id from source_versions where source_id = %s and file_id = %s",
                (target, file_id),
            ).fetchone()
            if duplicate:
                raise AppError(409, "DUPLICATE_NAME", "这份文件已经登记过，不会静默覆盖。")
            file_row = connection.execute("select media_type from files where id = %s", (file_id,)).fetchone()
            if file_row is None:
                raise AppError(422, "VALIDATION_FAILED", "找不到上传的文件。")
            latest = connection.execute(
                "select coalesce(max(version_number), 0) as version_number from source_versions where source_id = %s",
                (target,),
            ).fetchone()
            version_id = uuid4()
            connection.execute(
                """
                insert into source_versions (
                  id, source_id, version_number, file_id, media_type, status, message
                )
                values (%s, %s, %s, %s, %s, 'uploaded', %s)
                """,
                (
                    version_id,
                    target,
                    int(latest["version_number"]) + 1,
                    file_id,
                    file_row["media_type"],
                    "已上传新版本，尚未解析，旧版本仍然保留。",
                ),
            )
            connection.execute(
                "update sources set revision = revision + 1 where id = %s",
                (target,),
            )
            created = connection.execute(
                """
                select v.*, s.name, s.base_id, s.revision as source_revision
                from source_versions v join sources s on s.id = v.source_id
                where v.id = %s
                """,
                (version_id,),
            ).fetchone()
    return {"data": _public_version(created), "requestId": request.state.request_id}


@router.post("/sources/{source_id}/retire")
def retire_source(source_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    actor = require_permission(request, "knowledge.manage")
    target = _uuid(source_id, "sourceId")
    if not body.reason.strip():
        raise AppError(422, "VALIDATION_FAILED", "请填写原因。")
    with connect() as connection:
        with connection.transaction():
            source = connection.execute("select * from sources where id = %s", (target,)).fetchone()
            if source is None:
                raise AppError(404, "NOT_FOUND", "没有这份资料。")
            _require_writer(connection, source["base_id"], actor.user_id, _admin(actor.roles))
            if int(source["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这份资料刚被改过。", current_revision=int(source["revision"]))
            connection.execute(
                "update sources set state = 'retired', revision = revision + 1 where id = %s",
                (target,),
            )
            connection.execute(
                """
                update source_versions
                set status = 'retired', message = %s, revision = revision + 1, updated_at = now()
                where source_id = %s and status <> 'retired'
                """,
                ("已停用，记录仍保留，没有删除原件。", target),
            )
            latest = connection.execute(
                """
                select v.*, s.name, s.base_id, s.revision as source_revision
                from source_versions v join sources s on s.id = v.source_id
                where v.source_id = %s
                order by v.version_number desc
                limit 1
                """,
                (target,),
            ).fetchone()
    return {"data": _public_version(latest), "requestId": request.state.request_id}
