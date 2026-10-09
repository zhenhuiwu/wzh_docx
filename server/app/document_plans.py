"""Persist project document intentions and link documents by their current identity."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.identity import require_permission
from app.projects import _is_admin, _load, _parse_uuid, require_project_editor

router = APIRouter()

# Keep the plan list bounded until a project needs a larger document set.
_MAX_PLANS = 100
_PLAN_SELECT = """
    select p.id, p.name, p.purpose, p.template_id,
           t.name as template_name, t.status as template_status,
           d.id as document_id
    from document_plans p
    left join templates t on t.id = p.template_id
    left join documents d on d.project_id = p.project_id
                         and d.name = p.name
"""


class DocumentPlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=200)
    templateId: UUID | None = None


def _text(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AppError(422, "VALIDATION_FAILED", "请填写文档名称和用途。", [{"field": field, "message": "请填写。"}])
    return cleaned


def _public(row: dict) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "purpose": row["purpose"],
        "templateId": None if row["template_id"] is None else str(row["template_id"]),
        "templateName": row["template_name"],
        "templateStatus": row["template_status"],
        "documentId": None if row["document_id"] is None else str(row["document_id"]),
    }


@router.get("/projects/{project_id}/document-plans")
def list_document_plans(project_id: str, request: Request, page: int = 1, pageSize: int = 20) -> dict[str, object]:
    actor = require_permission(request, "documents.read")
    target = _parse_uuid(project_id, "projectId")
    if page < 1 or not 1 <= pageSize <= 50:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    with connect() as connection:
        _load(connection, target, actor.user_id, _is_admin(actor.roles))
        total = connection.execute(
            "select count(*) as total from document_plans where project_id = %s", (target,)
        ).fetchone()["total"]
        rows = connection.execute(
            _PLAN_SELECT + " where p.project_id = %s order by p.created_at, p.id limit %s offset %s",
            (target, pageSize, (page - 1) * pageSize),
        ).fetchall()
    return {
        "data": {"items": [_public(row) for row in rows], "page": page, "pageSize": pageSize, "total": int(total)},
        "requestId": request.state.request_id,
    }


@router.post("/projects/{project_id}/document-plans", status_code=201)
def create_document_plan(project_id: str, request: Request, body: DocumentPlanBody) -> dict[str, object]:
    actor = require_permission(request, "documents.edit")
    target = _parse_uuid(project_id, "projectId")
    name = _text(body.name, "name")
    purpose = _text(body.purpose, "purpose")
    plan_id = uuid4()
    with connect() as connection:
        with connection.transaction():
            _load(connection, target, actor.user_id, _is_admin(actor.roles))
            require_project_editor(connection, actor, target)
            # Serialize creates per project so the 100-plan ceiling cannot race.
            connection.execute("select id from projects where id = %s for update", (target,))
            if body.templateId is not None and connection.execute(
                "select id from templates where id = %s", (body.templateId,)
            ).fetchone() is None:
                raise AppError(422, "VALIDATION_FAILED", "找不到这个模板。", [{"field": "templateId", "message": "找不到这个模板。"}])
            if connection.execute(
                "select id from document_plans where project_id = %s and name = %s and purpose = %s",
                (target, name, purpose),
            ).fetchone():
                raise AppError(409, "DUPLICATE_NAME", "同一用途下已经有同名计划。")
            total = connection.execute(
                "select count(*) as total from document_plans where project_id = %s", (target,)
            ).fetchone()["total"]
            if total >= _MAX_PLANS:
                raise AppError(409, "LIMIT_REACHED", "每个项目最多只能建立100项文档计划。")
            connection.execute(
                """insert into document_plans (id, project_id, name, purpose, template_id)
                   values (%s, %s, %s, %s, %s)""",
                (plan_id, target, name, purpose, body.templateId),
            )
            row = connection.execute(_PLAN_SELECT + " where p.id = %s", (plan_id,)).fetchone()
    return {"data": _public(row), "requestId": request.state.request_id}
