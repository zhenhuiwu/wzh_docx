"""Workbench reads live projects, reviews, facts and jobs. It does not store a second status."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from app.db import connect
from app.documents import _uuid
from app.errors import AppError
from app.identity import require_permission
from app.jobs import _COLUMNS
from app.jobs import _public as job_public
from app.models import format_timestamp
from app.projects import _public as project_public

router = APIRouter()

_SORTS = {
    "name_asc": "title",
    "updatedAt_asc": "updated",
    "updatedAt_desc": "updated",
}
_KINDS = {"missing_material", "fact_confirmation", "review", "impact", "verification", "export_error"}
_ACTIVE = ("queued", "running", "pause_requested", "paused", "waiting_input", "needs_verification")
_TARGET_KINDS = {"block", "document", "source", "template_version", "test_case", "capture", "conversation", "delivery", "system"}


def _admin(roles: tuple[str, ...]) -> bool:
    return "system_admin" in roles


def _visible(admin: bool) -> str:
    if admin:
        return "true"
    return "exists (select 1 from project_members m where m.project_id = p.id and m.user_id = %s)"


def _title(text: str) -> str:
    cleaned = text.strip() or "待处理事项"
    return cleaned[:120]


def _issue(
    issue_id: str,
    project_id: str,
    project_name: str,
    title: str,
    kind: str,
    assignee: str | None,
    target_kind: str,
    target_name: str,
    updated_at: datetime,
    target_id: str | None = None,
) -> dict[str, object]:
    target: dict[str, object] = {"kind": target_kind if target_kind in _TARGET_KINDS else "system", "name": _title(target_name)}
    if target_id:
        target["id"] = target_id
    return {
        "id": issue_id,
        "projectId": project_id,
        "projectName": project_name,
        "title": _title(title),
        "kind": kind,
        "status": "open",
        "assigneeId": assignee,
        "target": target,
        "updatedAt": format_timestamp(updated_at),
        "_updated": updated_at,
    }


def _collect(connection, user_id, admin: bool) -> list[dict[str, object]]:
    visibility = _visible(admin)
    params: list[object] = [] if admin else [user_id]
    found: list[dict[str, object]] = []
    reviews = connection.execute(
        f"""
        select r.id, r.project_id, p.name as project_name, b.id as block_id, b.title as block_title,
               r.reviewer_id, r.updated_at
        from reviews r
        join projects p on p.id = r.project_id
        join block_revisions br on br.id = r.block_revision_id
        join document_blocks b on b.id = br.block_id
        where r.state = 'pending' and {visibility}
        """,
        params,
    ).fetchall()
    for row in reviews:
        found.append(
            _issue(
                str(row["id"]),
                str(row["project_id"]),
                row["project_name"],
                f"审核{row['block_title']}",
                "review",
                str(row["reviewer_id"]),
                "block",
                row["block_title"],
                row["updated_at"],
                str(row["block_id"]),
            )
        )
    blocks = connection.execute(
        f"""
        select b.id, d.project_id, p.name as project_name, b.title, d.updated_at
        from document_blocks b
        join documents d on d.id = b.document_id
        join projects p on p.id = d.project_id
        where b.validity = 'needs_review' and {visibility}
        """,
        params,
    ).fetchall()
    for row in blocks:
        found.append(
            _issue(
                str(row["id"]),
                str(row["project_id"]),
                row["project_name"],
                f"待复核{row['title']}",
                "fact_confirmation",
                None,
                "block",
                row["title"],
                row["updated_at"],
                str(row["id"]),
            )
        )
    facts = connection.execute(
        f"""
        select f.id, f.project_id, p.name as project_name, f.label, r.created_at
        from facts f
        join fact_revisions r on r.fact_id = f.id and r.revision = f.revision
        join projects p on p.id = f.project_id
        where r.state = 'candidate' and {visibility}
        """,
        params,
    ).fetchall()
    for row in facts:
        found.append(
            _issue(
                str(row["id"]),
                str(row["project_id"]),
                row["project_name"],
                f"待确认{row['label']}",
                "fact_confirmation",
                None,
                "system",
                row["label"],
                row["created_at"],
            )
        )
    job_visibility = "true" if admin else "(j.created_by = %s or j.project_id in (select project_id from project_members where user_id = %s))"
    job_params: list[object] = [] if admin else [user_id, user_id]
    jobs = connection.execute(
        f"""
        select j.id, j.project_id, p.name as project_name, j.target_kind, j.target_id, j.target_name,
               j.created_by, j.updated_at
        from jobs j
        left join projects p on p.id = j.project_id
        where j.status = 'needs_verification' and {job_visibility}
        """,
        job_params,
    ).fetchall()
    for row in jobs:
        project_id = row["project_id"]
        if project_id is None:
            continue
        found.append(
            _issue(
                str(row["id"]),
                str(project_id),
                row["project_name"] or "项目",
                f"待核实{row['target_name']}",
                "verification",
                str(row["created_by"]),
                row["target_kind"],
                row["target_name"],
                row["updated_at"],
                None if row["target_id"] is None else str(row["target_id"]),
            )
        )
    unique: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in found:
        target = item["target"]
        assert isinstance(target, dict)
        key = (str(item["kind"]), str(target["kind"]), str(target.get("id") or item["id"]))
        previous = unique.get(key)
        if previous is None or item["_updated"] > previous["_updated"]:
            unique[key] = item
    return list(unique.values())


def _strip(item: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key != "_updated"}


def _query(page: int, page_size: int, sort: str, kind: str, status: str, name: str, project_id: str) -> None:
    if page < 1 or not 1 <= page_size <= 50 or sort not in _SORTS:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    if kind and kind not in _KINDS:
        raise AppError(422, "VALIDATION_FAILED", "事项类型不正确。")
    if status and status not in {"open", "resolved"}:
        raise AppError(422, "VALIDATION_FAILED", "事项状态不正确。")
    if len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "名称过长。")
    if project_id:
        _uuid(project_id, "projectId")


@router.get("/issues")
def list_issues(
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    sort: str = "updatedAt_desc",
    projectId: str = "",
    kind: str = "",
    status: str = "",
    name: str = "",
) -> dict[str, object]:
    actor = require_permission(request, "projects.read")
    _query(page, pageSize, sort, kind, status, name, projectId)
    admin = _admin(actor.roles)
    with connect() as connection:
        if projectId:
            visible = connection.execute(
                f"select id from projects p where p.id = %s and {_visible(admin)}",
                (projectId, actor.user_id) if not admin else (projectId,),
            ).fetchone()
            if visible is None:
                raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
        items = [] if status == "resolved" else _collect(connection, actor.user_id, admin)
    if projectId:
        items = [item for item in items if item["projectId"] == projectId]
    if kind:
        items = [item for item in items if item["kind"] == kind]
    if name.strip():
        items = [item for item in items if name.strip() in str(item["title"])]
    reverse = sort != "updatedAt_asc"
    if sort == "name_asc":
        items.sort(key=lambda item: str(item["title"]))
    else:
        items.sort(key=lambda item: item["_updated"], reverse=reverse)
    total = len(items)
    start = (page - 1) * pageSize
    page_items = [_strip(item) for item in items[start : start + pageSize]]
    return {
        "data": {"items": page_items, "page": page, "pageSize": pageSize, "total": total},
        "requestId": request.state.request_id,
    }


@router.get("/workbench")
def get_workbench(request: Request) -> dict[str, object]:
    actor = require_permission(request, "projects.read")
    admin = _admin(actor.roles)
    params: list[object] = [] if admin else [actor.user_id]
    with connect() as connection:
        issues = _collect(connection, actor.user_id, admin)
        issues.sort(key=lambda item: item["_updated"], reverse=True)
        projects = connection.execute(
            f"""
            select p.id, p.name, p.description, p.organization, p.department, p.group_name, p.client_contact,
                   p.owner_id, owner.name as owner_name, p.period_start, p.period_end,
                   p.status, p.archived_at, p.revision, p.created_at, p.updated_at,
                   p.software_version
            from projects p
            join users owner on owner.id = p.owner_id
            where p.archived_at is null and {_visible(admin)}
            order by p.updated_at desc
            limit 8
            """,
            params,
        ).fetchall()
        job_visibility = "true" if admin else "(j.created_by = %s or j.project_id in (select project_id from project_members where user_id = %s))"
        job_params: list[object] = [] if admin else [actor.user_id, actor.user_id]
        jobs = connection.execute(
            f"""
            select {_COLUMNS}
            from jobs j
            where j.status in ({", ".join("%s" for _ in _ACTIVE)}) and {job_visibility}
            order by j.updated_at desc
            limit 10
            """,
            [*_ACTIVE, *job_params],
        ).fetchall()
    return {
        "data": {
            "issues": [_strip(item) for item in issues[:20]],
            "recentProjects": [project_public(row) for row in projects],
            "activeJobs": [job_public(row) for row in jobs],
            "updatedAt": format_timestamp(datetime_now()),
        },
        "requestId": request.state.request_id,
    }


def datetime_now():
    from datetime import timezone

    return datetime.now(timezone.utc)
