"""Reviews attach to one block revision. The author cannot approve that revision."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.documents import _admin, _document_row, _project_visible, _uuid
from app.errors import AppError
from app.identity import require_csrf, require_session
from app.models import format_timestamp
from app.permissions import has_permission

router = APIRouter()

_SORTS = {
    "name_asc": "d.name asc, r.updated_at desc",
    "updatedAt_asc": "r.updated_at asc",
    "updatedAt_desc": "r.updated_at desc",
}
_STATES = {"pending", "approved", "rejected"}


class ReviewCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blockRevisionId: str
    reviewerId: str
    expectedBlockRevision: int = Field(ge=0)


class ReviewDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    decision: str
    reason: str = ""


def _project_role(connection, project_id: UUID, user_id: UUID) -> str | None:
    row = connection.execute(
        "select role from project_members where project_id = %s and user_id = %s",
        (project_id, user_id),
    ).fetchone()
    return None if row is None else row["role"]


def _holds(connection, roles: tuple[str, ...], project_id: UUID, user_id: UUID, permission: str) -> bool:
    if has_permission(roles, permission):
        return True
    role = _project_role(connection, project_id, user_id)
    return role == "reviewer" and permission in {"reviews.read", "reviews.decide"}


def _user_roles(connection, user_id: UUID) -> tuple[str, ...]:
    rows = connection.execute("select role from user_roles where user_id = %s", (user_id,)).fetchall()
    return tuple(row["role"] for row in rows)


def _actor(request: Request, mutating: bool):
    actor = require_session(request)
    if mutating:
        require_csrf(request, actor)
    return actor


def _public(row: dict) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "projectId": str(row["project_id"]),
        "documentId": str(row["document_id"]),
        "blockRevisionId": str(row["block_revision_id"]),
        "reviewerId": str(row["reviewer_id"]),
        "state": row["state"],
        "reason": row["reason"],
        "revision": row["revision"],
        "updatedAt": format_timestamp(row["updated_at"]),
        "documentName": row["document_name"],
        "blockTitle": row["block_title"],
        "reviewerName": row["reviewer_name"],
        "submitterId": str(row["submitter_id"]),
        "submitterName": row["submitter_name"],
        "createdAt": format_timestamp(row["created_at"]),
    }


def _load_review(connection, review_id: UUID) -> dict | None:
    return connection.execute(
        """
        select r.id, r.project_id, r.document_id, r.block_revision_id, r.submitter_id, r.reviewer_id,
               r.state, r.reason, r.revision, r.created_at, r.updated_at,
               d.name as document_name, b.title as block_title,
               su.name as submitter_name, ru.name as reviewer_name,
               br.created_by as revision_author_id, br.block_id
        from reviews r
        join documents d on d.id = r.document_id
        join block_revisions br on br.id = r.block_revision_id
        join document_blocks b on b.id = br.block_id
        join users su on su.id = r.submitter_id
        join users ru on ru.id = r.reviewer_id
        where r.id = %s
        """,
        (review_id,),
    ).fetchone()


@router.get("/projects/{project_id}/reviews")
def list_reviews(
    project_id: str,
    request: Request,
    page: int = 1,
    pageSize: int = 10,
    sort: str = "updatedAt_desc",
    state: str = "",
    name: str = "",
) -> dict[str, object]:
    actor = _actor(request, False)
    target = _uuid(project_id, "projectId")
    if page < 1 or not 1 <= pageSize <= 50 or sort not in _SORTS:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    if state and state not in _STATES:
        raise AppError(422, "VALIDATION_FAILED", "审核状态不正确。")
    if len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "名称过长。")
    admin = _admin(actor.roles)
    filters = ["r.project_id = %s"]
    params: list[object] = [target]
    if state:
        filters.append("r.state = %s")
        params.append(state)
    if name.strip():
        filters.append("(d.name ilike %s or b.title ilike %s)")
        params.extend([f"%{name.strip()}%", f"%{name.strip()}%"])
    where = " and ".join(filters)
    with connect() as connection:
        _project_visible(connection, target, actor.user_id, admin)
        if not _holds(connection, actor.roles, target, actor.user_id, "reviews.read"):
            raise AppError(403, "FORBIDDEN", "没有查看审核的权限。")
        total = connection.execute(
            f"""
            select count(*) as total
            from reviews r
            join documents d on d.id = r.document_id
            join block_revisions br on br.id = r.block_revision_id
            join document_blocks b on b.id = br.block_id
            where {where}
            """,
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            select r.id, r.project_id, r.document_id, r.block_revision_id, r.submitter_id, r.reviewer_id,
                   r.state, r.reason, r.revision, r.created_at, r.updated_at,
                   d.name as document_name, b.title as block_title,
                   su.name as submitter_name, ru.name as reviewer_name
            from reviews r
            join documents d on d.id = r.document_id
            join block_revisions br on br.id = r.block_revision_id
            join document_blocks b on b.id = br.block_id
            join users su on su.id = r.submitter_id
            join users ru on ru.id = r.reviewer_id
            where {where}
            order by {_SORTS[sort]}
            limit %s offset %s
            """,
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    return {
        "data": {"items": [_public(row) for row in rows], "page": page, "pageSize": pageSize, "total": total},
        "requestId": request.state.request_id,
    }


@router.post("/reviews", status_code=201)
def create_review(request: Request, body: ReviewCreateBody) -> dict[str, object]:
    actor = _actor(request, True)
    if not has_permission(actor.roles, "reviews.submit"):
        raise AppError(403, "FORBIDDEN", "没有提交审核的权限。")
    revision_id = _uuid(body.blockRevisionId, "blockRevisionId")
    reviewer_id = _uuid(body.reviewerId, "reviewerId")
    if reviewer_id == actor.user_id:
        raise AppError(422, "VALIDATION_FAILED", "不能提交给自己审核。")
    with connect() as connection:
        with connection.transaction():
            revision = connection.execute(
                """
                select br.id, br.block_id, br.disposition, br.review_state, br.created_by,
                       b.document_id, b.revision as block_revision, d.project_id
                from block_revisions br
                join document_blocks b on b.id = br.block_id
                join documents d on d.id = b.document_id
                where br.id = %s
                for update
                """,
                (revision_id,),
            ).fetchone()
            if revision is None:
                raise AppError(404, "NOT_FOUND", "没有这个修订。")
            _document_row(connection, revision["document_id"], actor.user_id, _admin(actor.roles))
            if int(revision["block_revision"]) != body.expectedBlockRevision:
                raise AppError(
                    409,
                    "REVISION_CONFLICT",
                    "这个内容块刚被保存过。请看过新稿后再提交。",
                    current_revision=int(revision["block_revision"]),
                )
            if revision["disposition"] not in {"current", "superseded"}:
                raise AppError(409, "INVALID_TRANSITION", "请先保存稿件，再提交这一份修订。")
            if revision["review_state"] == "approved":
                raise AppError(409, "INVALID_TRANSITION", "这份修订已经批准。")
            if reviewer_id == revision["created_by"]:
                raise AppError(422, "VALIDATION_FAILED", "不能让编写人审核自己的修订。")
            reviewer = connection.execute(
                "select id, status from users where id = %s",
                (reviewer_id,),
            ).fetchone()
            if reviewer is None or reviewer["status"] != "active":
                raise AppError(422, "VALIDATION_FAILED", "请选择有效的审核人。")
            if not _holds(connection, _user_roles(connection, reviewer_id), revision["project_id"], reviewer_id, "reviews.decide"):
                raise AppError(422, "VALIDATION_FAILED", "这个人不能审核当前项目。")
            pending = connection.execute(
                """
                select id from reviews
                where block_revision_id = %s and state = 'pending'
                """,
                (revision_id,),
            ).fetchone()
            if pending:
                raise AppError(409, "INVALID_TRANSITION", "这份修订已经在审核中。")
            review_id = uuid4()
            connection.execute(
                """
                insert into reviews (
                  id, project_id, document_id, block_revision_id, submitter_id, reviewer_id, state
                )
                values (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    review_id,
                    revision["project_id"],
                    revision["document_id"],
                    revision_id,
                    actor.user_id,
                    reviewer_id,
                ),
            )
            connection.execute(
                "update block_revisions set review_state = 'pending_review' where id = %s and review_state <> 'approved'",
                (revision_id,),
            )
            created = _load_review(connection, review_id)
    return {"data": _public(created), "requestId": request.state.request_id}


@router.post("/reviews/{review_id}/decision")
def decide_review(review_id: str, request: Request, body: ReviewDecisionBody) -> dict[str, object]:
    actor = _actor(request, True)
    target = _uuid(review_id, "reviewId")
    if body.decision not in {"approve", "reject"}:
        raise AppError(422, "VALIDATION_FAILED", "请选择通过或退回。")
    if body.decision == "reject" and (not body.reason.strip() or len(body.reason) > 1000):
        raise AppError(422, "VALIDATION_FAILED", "退回需要填写不超过 1000 个字的理由。")
    if len(body.reason) > 1000:
        raise AppError(422, "VALIDATION_FAILED", "理由不能超过 1000 个字。")
    with connect() as connection:
        with connection.transaction():
            review = connection.execute(
                "select id from reviews where id = %s for update",
                (target,),
            ).fetchone()
            if review is None:
                raise AppError(404, "NOT_FOUND", "没有这个审核。")
            row = _load_review(connection, target)
            _document_row(connection, row["document_id"], actor.user_id, _admin(actor.roles))
            if not _holds(connection, actor.roles, row["project_id"], actor.user_id, "reviews.decide"):
                raise AppError(403, "FORBIDDEN", "没有审核决定的权限。")
            if actor.user_id == row["revision_author_id"] or actor.user_id == row["submitter_id"]:
                raise AppError(403, "FORBIDDEN", "不能审核自己编写或提交的修订。")
            if actor.user_id != row["reviewer_id"]:
                raise AppError(403, "FORBIDDEN", "这份审核指定了其他审核人。")
            if int(row["revision"]) != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "这个审核刚被处理过。", current_revision=int(row["revision"]))
            if row["state"] != "pending":
                raise AppError(409, "INVALID_TRANSITION", "这个审核已经有决定。")
            next_state = "approved" if body.decision == "approve" else "rejected"
            connection.execute(
                """
                update reviews
                set state = %s, reason = %s, revision = revision + 1, updated_at = now()
                where id = %s
                """,
                (next_state, body.reason.strip(), target),
            )
            if body.decision == "approve":
                connection.execute(
                    "update block_revisions set review_state = 'approved' where id = %s",
                    (row["block_revision_id"],),
                )
                connection.execute(
                    "update document_blocks set approved_revision_id = %s where id = %s",
                    (row["block_revision_id"], row["block_id"]),
                )
            else:
                connection.execute(
                    "update block_revisions set review_state = 'rejected' where id = %s",
                    (row["block_revision_id"],),
                )
                connection.execute(
                    """
                    update document_blocks
                    set approved_revision_id = null
                    where id = %s and approved_revision_id = %s
                    """,
                    (row["block_id"], row["block_revision_id"]),
                )
            updated = _load_review(connection, target)
    return {"data": _public(updated), "requestId": request.state.request_id}
