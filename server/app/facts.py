"""Project facts stay versioned. Bindings never copy a knowledge base the caller cannot see."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.identity import require_permission, require_session
from app.knowledge import _admin, _can_see, _require_base
from app.projects import require_project_owner
from app.models import format_timestamp

router = APIRouter()


class FactCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: str | int | float | bool | None
    sourceVersionIds: list[str] = Field(default_factory=list)
    decisionReason: str = ""


class FactPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    value: str | int | float | bool | None
    sourceVersionIds: list[str] = Field(default_factory=list)
    decisionReason: str = ""


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    reason: str


class BindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedProjectRevision: int = Field(ge=0)
    baseIds: list[str]


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(422, "VALIDATION_FAILED", "标识不正确。", [{"field": field, "message": "需要有效标识。"}])


def _text_value(value: str | int | float | bool | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _typed_value(text: str | None) -> str | None:
    return text


def _auditor(roles) -> bool:
    """全局审计员：只读接口按可见处理（写接口不用）。"""
    return "auditor" in roles


def _visible_project(connection, project_id: UUID, user_id: UUID, admin: bool) -> dict:
    params: list[object] = [project_id]
    clause = "true"
    if not admin:
        clause = "exists (select 1 from project_members m where m.project_id = p.id and m.user_id = %s)"
        params.append(user_id)
    row = connection.execute(
        f"select id, revision, software_version from projects p where p.id = %s and {clause}",
        params,
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
    return row


def _usable_sources(connection, source_ids: list[UUID], user_id: UUID, admin: bool, project_id: UUID) -> list[dict]:
    found = []
    for source_id in source_ids:
        row = connection.execute(
            """
            select v.id, v.version_number, s.name, s.base_id
            from source_versions v
            join sources s on s.id = v.source_id
            where v.id = %s
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            raise AppError(404, "NOT_FOUND", "找不到这个资料版本。")
        try:
            _require_base(connection, row["base_id"], user_id, admin)
        except AppError:
            raise AppError(404, "NOT_FOUND", "找不到这个资料版本。") from None
        base = connection.execute(
            "select scope, project_id from knowledge_bases where id = %s",
            (row["base_id"],),
        ).fetchone()
        if base is not None and base["scope"] == "project" and base["project_id"] != project_id:
            raise AppError(404, "NOT_FOUND", "这个资料不属于当前项目可用来源。")
        bound = connection.execute(
            """
            select 1 from project_knowledge_bindings
            where project_id = %s and base_id = %s
            """,
            (project_id, row["base_id"]),
        ).fetchone()
        if bound is None:
            raise AppError(404, "NOT_FOUND", "这个资料不属于当前项目可用来源。")
        found.append(row)
    return found


def _citations(connection, revision_id: UUID, user_id: UUID, admin: bool) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        select v.id, v.version_number, s.name, s.base_id
        from fact_sources fs
        join source_versions v on v.id = fs.source_version_id
        join sources s on s.id = v.source_id
        where fs.revision_id = %s
        """,
        (revision_id,),
    ).fetchall()
    citations: list[dict[str, object]] = []
    for row in rows:
        visible = admin or _can_see(connection, row["base_id"], user_id, False) is not None
        citations.append(
            {
                "sourceVersionId": str(row["id"]),
                "chunkId": str(row["id"]),
                "sourceName": row["name"] if visible else "受限资料",
                "sourceVersion": str(row["version_number"]) if visible else "",
                "headingPath": [],
                "excerpt": "",
                "locatorVerified": False,
            }
        )
    return citations


def _latest(connection, fact_id: UUID) -> dict:
    return connection.execute(
        """
        select f.id, f.project_id, f.fact_key, f.label, f.revision, f.updated_at,
               r.id as revision_id, r.value_text, r.state, r.decision_reason
        from facts f
        join fact_revisions r on r.fact_id = f.id and r.revision = f.revision
        where f.id = %s
        """,
        (fact_id,),
    ).fetchone()


def _public(connection, row: dict, user_id: UUID, admin: bool) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "projectId": str(row["project_id"]),
        "key": row["fact_key"],
        "label": row["label"],
        "value": _typed_value(row["value_text"]),
        "state": row["state"],
        "revision": row["revision"],
        "sources": _citations(connection, row["revision_id"], user_id, admin),
        "decisionReason": row["decision_reason"],
        "updatedAt": format_timestamp(row["updated_at"]),
    }


def _mark_downstream(connection, project_id: UUID) -> None:
    connection.execute(
        """
        update documents
        set validity = 'needs_review', updated_at = now()
        where project_id = %s and validity = 'current'
        """,
        (project_id,),
    )
    connection.execute(
        """
        update document_blocks
        set validity = 'needs_review'
        where document_id in (select id from documents where project_id = %s)
          and validity = 'current'
        """,
        (project_id,),
    )


_SORTS = {
    "name_asc": "f.label asc, r.revision asc",
    "updatedAt_asc": "r.created_at asc, f.label asc",
    "updatedAt_desc": "r.created_at desc, f.label asc",
}
_STATES = {"candidate", "confirmed", "superseded"}


def _list_args(page: int, page_size: int, sort: str, state: str, name: str) -> tuple[int, int, str, str, str]:
    if page < 1 or not 1 <= page_size <= 50:
        raise AppError(422, "VALIDATION_FAILED", "页码或每页数量不正确。")
    if sort not in _SORTS:
        raise AppError(422, "VALIDATION_FAILED", "排序方式不正确。")
    if state and state not in _STATES:
        raise AppError(422, "VALIDATION_FAILED", "确认状态不正确。")
    if len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "名称过长。")
    return page, page_size, sort, state, name.strip()


@router.get("/projects/{project_id}/facts")
def list_facts(
    project_id: str,
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    sort: str = "name_asc",
    state: str = "",
    name: str = "",
) -> dict[str, object]:
    actor = require_session(request)  # 终审建议：与项目/文档接口一致，按项目成员可见（不再要求全局 projects.read）
    target = _uuid(project_id, "projectId")
    page, page_size, sort, state, name = _list_args(page, pageSize, sort, state, name)
    admin = _admin(actor.roles)
    filters = ["f.project_id = %s"]
    params: list[object] = [target]
    if state:
        filters.append("r.state = %s")
        params.append(state)
    else:
        filters.append("r.revision = f.revision")
    if name:
        filters.append("f.label ilike %s")
        params.append(f"%{name}%")
    where = " and ".join(filters)
    with connect() as connection:
        # 终验回归（低）：全局审计员与项目/文档接口一致，非成员也可只读查看（来源可见性仍按普通用户判断）
        _visible_project(connection, target, actor.user_id, admin or _auditor(actor.roles))
        total = connection.execute(
            f"""
            select count(*) as total
            from facts f
            join fact_revisions r on r.fact_id = f.id
            where {where}
            """,
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            select f.id, f.project_id, f.fact_key, f.label, r.revision, r.created_at as updated_at,
                   r.id as revision_id, r.value_text, r.state, r.decision_reason
            from facts f
            join fact_revisions r on r.fact_id = f.id
            where {where}
            order by {_SORTS[sort]}
            limit %s offset %s
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = [_public(connection, row, actor.user_id, admin) for row in rows]
    return {
        "data": {"items": items, "page": page, "pageSize": page_size, "total": total},
        "requestId": request.state.request_id,
    }


@router.post("/projects/{project_id}/facts", status_code=201)
def create_candidate(project_id: str, request: Request, body: FactCreateBody) -> dict[str, object]:
    actor = require_permission(request, "facts.edit")
    target = _uuid(project_id, "projectId")
    key = body.key.strip()
    label = body.label.strip()
    if not key or not label or len(key) > 100 or len(label) > 120:
        raise AppError(422, "VALIDATION_FAILED", "请填写不超过 120 个字的事实名称。")
    if len(body.decisionReason) > 1000:
        raise AppError(422, "VALIDATION_FAILED", "依据不能超过 1000 个字。")
    if len(body.sourceVersionIds) > 100:
        raise AppError(422, "VALIDATION_FAILED", "来源过多。")
    if body.value is None or (isinstance(body.value, str) and not body.value.strip()):
        raise AppError(422, "VALIDATION_FAILED", "请填写事实的值。")
    source_ids = [_uuid(item, "sourceVersionIds") for item in body.sourceVersionIds]
    admin = _admin(actor.roles)
    with connect() as connection:
        with connection.transaction():
            _visible_project(connection, target, actor.user_id, admin)
            sources = _usable_sources(connection, source_ids, actor.user_id, admin, target)
            if not sources and not body.decisionReason.strip():
                raise AppError(422, "VALIDATION_FAILED", "请填写来源或人工确认依据。")
            fact = connection.execute(
                "select id, revision from facts where project_id = %s and fact_key = %s",
                (target, key),
            ).fetchone()
            if fact is not None:
                raise AppError(
                    409,
                    "REVISION_CONFLICT",
                    "这个事实已经有了，请在原记录上提出修改。",
                    current_revision=int(fact["revision"]),
                )
            fact_id = uuid4()
            revision = 0
            connection.execute(
                "insert into facts (id, project_id, fact_key, label) values (%s, %s, %s, %s)",
                (fact_id, target, key, label),
            )
            revision_id = uuid4()
            connection.execute(
                """
                insert into fact_revisions (id, fact_id, revision, value_text, state, decision_reason)
                values (%s, %s, %s, %s, 'candidate', %s)
                """,
                (revision_id, fact_id, revision, _text_value(body.value), body.decisionReason.strip()),
            )
            for source in sources:
                connection.execute(
                    "insert into fact_sources (revision_id, source_version_id) values (%s, %s)",
                    (revision_id, source["id"]),
                )
            created = _latest(connection, fact_id)
            payload = _public(connection, created, actor.user_id, admin)
    return {"data": payload, "requestId": request.state.request_id}


@router.patch("/facts/{fact_id}")
def revise_fact(fact_id: str, request: Request, body: FactPatchBody) -> dict[str, object]:
    actor = require_permission(request, "facts.edit")
    target = _uuid(fact_id, "factId")
    if len(body.decisionReason) > 1000:
        raise AppError(422, "VALIDATION_FAILED", "依据不能超过 1000 个字。")
    if len(body.sourceVersionIds) > 100:
        raise AppError(422, "VALIDATION_FAILED", "来源过多。")
    if body.value is None or (isinstance(body.value, str) and not body.value.strip()):
        raise AppError(422, "VALIDATION_FAILED", "请填写事实的值。")
    source_ids = [_uuid(item, "sourceVersionIds") for item in body.sourceVersionIds]
    admin = _admin(actor.roles)
    with connect() as connection:
        with connection.transaction():
            fact = connection.execute("select * from facts where id = %s", (target,)).fetchone()
            if fact is None:
                raise AppError(404, "NOT_FOUND", "没有这个事实。")
            _visible_project(connection, fact["project_id"], actor.user_id, admin)
            if int(fact["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个事实刚被改过。", current_revision=int(fact["revision"]))
            sources = _usable_sources(connection, source_ids, actor.user_id, admin, fact["project_id"])
            if not sources and not body.decisionReason.strip():
                raise AppError(422, "VALIDATION_FAILED", "请填写来源或人工确认依据。")
            revision = int(fact["revision"]) + 1
            revision_id = uuid4()
            connection.execute(
                """
                update fact_revisions
                set state = 'superseded'
                where fact_id = %s and state = 'candidate'
                """,
                (target,),
            )
            connection.execute(
                "update facts set revision = %s, updated_at = now() where id = %s",
                (revision, target),
            )
            connection.execute(
                """
                insert into fact_revisions (id, fact_id, revision, value_text, state, decision_reason)
                values (%s, %s, %s, %s, 'candidate', %s)
                """,
                (revision_id, target, revision, _text_value(body.value), body.decisionReason.strip()),
            )
            for source in sources:
                connection.execute(
                    "insert into fact_sources (revision_id, source_version_id) values (%s, %s)",
                    (revision_id, source["id"]),
                )
            updated = _latest(connection, target)
            payload = _public(connection, updated, actor.user_id, admin)
    return {"data": payload, "requestId": request.state.request_id}


@router.post("/facts/{fact_id}/confirm")
def confirm_fact(fact_id: str, request: Request, body: ReasonBody) -> dict[str, object]:
    actor = require_permission(request, "facts.confirm")
    target = _uuid(fact_id, "factId")
    if not body.reason.strip() or len(body.reason) > 1000:
        raise AppError(422, "VALIDATION_FAILED", "请填写不超过 1000 个字的确认说明。")
    admin = _admin(actor.roles)
    with connect() as connection:
        with connection.transaction():
            fact = connection.execute("select * from facts where id = %s for update", (target,)).fetchone()
            if fact is None:
                raise AppError(404, "NOT_FOUND", "没有这个事实。")
            _visible_project(connection, fact["project_id"], actor.user_id, admin)
            if int(fact["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个事实刚被改过。", current_revision=int(fact["revision"]))
            current = connection.execute(
                "select * from fact_revisions where fact_id = %s and revision = %s",
                (target, fact["revision"]),
            ).fetchone()
            if current is None or current["state"] != "candidate":
                raise AppError(409, "INVALID_TRANSITION", "没有待确认的修改。")
            previous = connection.execute(
                """
                select id from fact_revisions
                where fact_id = %s and state = 'confirmed'
                """,
                (target,),
            ).fetchone()
            connection.execute(
                """
                update fact_revisions
                set state = 'superseded'
                where fact_id = %s and state = 'confirmed'
                """,
                (target,),
            )
            connection.execute(
                """
                update fact_revisions
                set state = 'confirmed', decision_reason = %s
                where id = %s
                """,
                (body.reason.strip(), current["id"]),
            )
            if previous is not None:
                _mark_downstream(connection, fact["project_id"])
            if fact["fact_key"] == "software_version" and current["value_text"]:
                connection.execute(
                    "update projects set software_version = %s, updated_at = now() where id = %s",
                    (current["value_text"], fact["project_id"]),
                )
            updated = _latest(connection, target)
            payload = _public(connection, updated, actor.user_id, admin)
    return {"data": payload, "requestId": request.state.request_id}


def _binding_item(connection, row: dict, user_id: UUID, admin: bool) -> dict[str, object]:
    visible = admin or _can_see(connection, row["id"], user_id, False) is not None
    return {
        "baseId": str(row["id"]),
        "name": row["name"] if visible else "受限资料",
        "revision": row["revision"],
    }


@router.get("/projects/{project_id}/knowledge-bindings")
def list_bindings(project_id: str, request: Request) -> dict[str, object]:
    actor = require_session(request)  # 终审建议：与项目/文档接口一致，按项目成员可见（不再要求全局 projects.read）
    target = _uuid(project_id, "projectId")
    admin = _admin(actor.roles)
    with connect() as connection:
        project = _visible_project(connection, target, actor.user_id, admin or _auditor(actor.roles))
        rows = connection.execute(
            """
            select b.id, b.name, b.revision
            from project_knowledge_bindings k
            join knowledge_bases b on b.id = k.base_id
            where k.project_id = %s
            order by b.name
            """,
            (target,),
        ).fetchall()
        items = [_binding_item(connection, row, actor.user_id, admin) for row in rows]
    return {
        "data": {
            "items": items,
            "projectRevision": project["revision"],
        },
        "requestId": request.state.request_id,
    }


@router.put("/projects/{project_id}/knowledge-bindings")
def set_bindings(project_id: str, request: Request, body: BindingBody) -> dict[str, object]:
    actor = require_permission(request, "projects.manage")
    target = _uuid(project_id, "projectId")
    admin = _admin(actor.roles)
    base_ids = [_uuid(item, "baseIds") for item in body.baseIds]
    with connect() as connection:
        with connection.transaction():
            project = _visible_project(connection, target, actor.user_id, admin)
            require_project_owner(connection, actor, target)
            if int(project["revision"]) != body.expectedProjectRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个项目刚被改过。", current_revision=int(project["revision"]))
            if len(base_ids) > 100:
                raise AppError(422, "VALIDATION_FAILED", "关联的知识库过多。")
            names = []
            for base_id in base_ids:
                row = _require_base(connection, base_id, actor.user_id, admin)
                if row["scope"] == "project" and row["project_id"] != target:
                    raise AppError(404, "NOT_FOUND", "没有这个知识库。")
                names.append(row)
            connection.execute("delete from project_knowledge_bindings where project_id = %s", (target,))
            for row in names:
                connection.execute(
                    "insert into project_knowledge_bindings (project_id, base_id) values (%s, %s)",
                    (target, row["id"]),
                )
            connection.execute(
                "update projects set revision = revision + 1, updated_at = now() where id = %s",
                (target,),
            )
            project = _visible_project(connection, target, actor.user_id, True)
            rows = connection.execute(
                """
                select b.id, b.name, b.revision
                from project_knowledge_bindings k
                join knowledge_bases b on b.id = k.base_id
                where k.project_id = %s
                order by b.name
                """,
                (target,),
            ).fetchall()
            items = [_binding_item(connection, row, actor.user_id, admin) for row in rows]
    return {
        "data": {
            "items": items,
            "projectRevision": project["revision"],
        },
        "requestId": request.state.request_id,
    }
