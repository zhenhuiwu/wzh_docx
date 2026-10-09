"""Job queries and control. Needs-verification is not treated as paused or cancelled."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.identity import require_permission
from app.job_events import append, since
from app.models import format_timestamp

router = APIRouter()

_ACTIVE = {"queued", "running", "pause_requested", "paused", "cancel_requested", "waiting_input"}


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)


class VerifyBody(RevisionBody):
    observedOutcome: str
    evidenceIds: list[str] = Field(default_factory=list)
    reason: str


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "任务标识不正确。",
            [{"field": field, "message": "需要有效标识。"}],
        ) from None


def _actions(status: str, cancel_intent: bool) -> list[str]:
    if status == "needs_verification":
        actions = ["verify"]
        if not cancel_intent:
            actions.append("cancel")
        return actions
    if status in {"queued", "running", "pause_requested"}:
        return ["pause", "cancel"]
    if status == "paused":
        return ["resume", "cancel"]
    if status == "waiting_input":
        return ["cancel"]
    return []


def _public(row: dict) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "projectId": None if row["project_id"] is None else str(row["project_id"]),
        "kind": row["kind"],
        "status": row["status"],
        "revision": row["revision"],
        "target": {
            "kind": row["target_kind"],
            "name": row["target_name"],
            **({} if row["target_id"] is None else {"id": str(row["target_id"])}),
        },
        "stageName": row["stage_name"],
        "completedSteps": row["completed_steps"],
        "totalSteps": row["total_steps"],
        "lastSequence": row["last_sequence"],
        "allowedActions": _actions(row["status"], bool(row["cancel_intent"])),
        "blockingReason": row["blocking_reason"],
        "resultRevisionId": None if row["result_revision_id"] is None else str(row["result_revision_id"]),
        "artifactFileIds": [],
        "startedAt": None if row["started_at"] is None else format_timestamp(row["started_at"]),
        "endedAt": None if row["ended_at"] is None else format_timestamp(row["ended_at"]),
    }


_COLUMNS = """
    id, project_id, kind, status, revision, target_kind, target_id, target_name,
    stage_name, completed_steps, total_steps, last_sequence, blocking_reason,
    result_revision_id, started_at, ended_at, cancel_intent, created_by, attempt
"""


def _visible(admin: bool) -> str:
    if admin:
        return "true"
    return "(j.created_by = %s or j.project_id in (select project_id from project_members where user_id = %s))"


def _load(connection, job_id: UUID, user_id: UUID, admin: bool) -> dict:
    params: list[object] = [job_id]
    visibility = "true"
    if not admin:
        visibility = _visible(False)
        params.extend([user_id, user_id])
    row = connection.execute(
        f"select {_COLUMNS} from jobs j where j.id = %s and {visibility}",
        params,
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个任务。")
    return row


def _revision(current: int, expected: int) -> None:
    if current != expected:
        raise AppError(
            409,
            "REVISION_CONFLICT",
            "这个任务刚被改过，请重新读取。",
            current_revision=current,
        )


@router.get("/jobs")
def list_jobs(
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    projectId: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    actor = require_permission(request, "jobs.read")
    if page < 1 or not 1 <= pageSize <= 50:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    admin = "system_admin" in actor.roles
    filters = ["true"]
    params: list[object] = []
    if not admin:
        filters.append(_visible(False))
        params.extend([actor.user_id, actor.user_id])
    if projectId:
        filters.append("j.project_id = %s")
        params.append(_parse_uuid(projectId, "projectId"))
    if status:
        filters.append("j.status = %s")
        params.append(status)
    where = " and ".join(filters)
    with connect() as connection:
        total_row = connection.execute(
            f"select count(*) as total from jobs j where {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"""
            select {_COLUMNS}
            from jobs j
            where {where}
            order by j.created_at desc
            limit %s offset %s
            """,
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    return {
        "data": {
            "items": [_public(row) for row in rows],
            "page": page,
            "pageSize": pageSize,
            "total": int(total_row["total"]) if total_row else 0,
        },
        "requestId": request.state.request_id,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    actor = require_permission(request, "jobs.read")
    target = _parse_uuid(job_id, "jobId")
    with connect() as connection:
        row = _load(connection, target, actor.user_id, "system_admin" in actor.roles)
    return {"data": _public(row), "requestId": request.state.request_id}


@router.get("/jobs/{job_id}/events")
def stream_events(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
    actor = require_permission(request, "jobs.read")
    target = _parse_uuid(job_id, "jobId")
    with connect() as connection:
        row = _load(connection, target, actor.user_id, "system_admin" in actor.roles)
        events = since(connection, target, after)
        snapshot = _public(row)

    def chunks():
        yield f"event: snapshot\ndata: {snapshot['status']}\n\n"
        for event in events:
            yield (
                "event: job\n"
                f"id: {event['sequence']}\n"
                f"data: {event['sequence']} {event['type']} {event['status']}\n\n"
            )

    return StreamingResponse(chunks(), media_type="text/event-stream")


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, request: Request, body: RevisionBody) -> dict[str, object]:
    return _control(job_id, request, body.expectedRevision, "pause")


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, request: Request, body: RevisionBody) -> dict[str, object]:
    return _control(job_id, request, body.expectedRevision, "resume")


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, body: RevisionBody) -> dict[str, object]:
    return _control(job_id, request, body.expectedRevision, "cancel")


def _control(job_id: str, request: Request, expected: int, action: str) -> dict[str, object]:
    actor = require_permission(request, "jobs.control")
    target = _parse_uuid(job_id, "jobId")
    admin = "system_admin" in actor.roles
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = _load(connection, target, actor.user_id, admin)
            _revision(int(row["revision"]), expected)
            status = row["status"]
            if action == "resume" and status == "needs_verification":
                raise AppError(409, "INVALID_TRANSITION", "结果还没核实，不能重新执行。")
            if action == "cancel" and status == "needs_verification":
                connection.execute(
                    """
                    update jobs
                    set cancel_intent = true, revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
                append(connection, target, "status", status, row["stage_name"], "已记下取消意图")
            elif action == "pause" and status == "running":
                connection.execute(
                    """
                    update jobs
                    set status = 'pause_requested', revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
            elif action == "pause" and status == "queued":
                connection.execute(
                    """
                    update jobs
                    set status = 'paused', revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
            elif action == "resume" and status == "paused":
                connection.execute(
                    """
                    update jobs
                    set status = 'queued', lease_until = null, revision = revision + 1, updated_at = now()
                    where id = %s
                    """,
                    (target,),
                )
            elif action == "cancel" and status in _ACTIVE:
                next_status = "cancelled" if status in {"queued", "paused"} else "cancel_requested"
                connection.execute(
                    """
                    update jobs
                    set status = %s,
                        ended_at = case when %s = 'cancelled' then now() else ended_at end,
                        revision = revision + 1,
                        updated_at = now()
                    where id = %s
                    """,
                    (next_status, next_status, target),
                )
            else:
                raise AppError(409, "INVALID_TRANSITION", "当前状态不能做这个操作。")
            updated = _load(connection, target, actor.user_id, admin)
    return {"data": _public(updated), "requestId": request.state.request_id}


@router.post("/jobs/{job_id}/verify")
def verify_job(job_id: str, request: Request, body: VerifyBody) -> dict[str, object]:
    actor = require_permission(request, "jobs.verify")
    target = _parse_uuid(job_id, "jobId")
    if body.observedOutcome not in {"effect_applied", "effect_not_applied", "still_unknown"}:
        raise AppError(422, "VALIDATION_FAILED", "核实结果不正确。")
    if not body.reason.strip():
        raise AppError(422, "VALIDATION_FAILED", "请填写核实说明。")
    admin = "system_admin" in actor.roles
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = _load(connection, target, actor.user_id, admin)
            _revision(int(row["revision"]), body.expectedRevision)
            if row["status"] != "needs_verification":
                raise AppError(409, "INVALID_TRANSITION", "这个任务现在不需要核实。")
            if body.observedOutcome == "still_unknown":
                next_status = "needs_verification"
            elif body.observedOutcome == "effect_applied":
                next_status = "cancelled" if row["cancel_intent"] else "succeeded"
            else:
                next_status = "failed"
            connection.execute(
                """
                update jobs
                set status = %s,
                    blocking_reason = %s,
                    ended_at = case when %s = 'needs_verification' then null else now() end,
                    revision = revision + 1,
                    updated_at = now()
                where id = %s
                """,
                (next_status, body.reason.strip(), next_status, target),
            )
            append(connection, target, "completed", next_status, row["stage_name"], body.reason.strip())
            updated = _load(connection, target, actor.user_id, admin)
    return {"data": _public(updated), "requestId": request.state.request_id}
