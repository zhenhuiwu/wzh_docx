"""「我的编写」：当前用户负责的全部文档（30 §3.2）。

“负责”= 文档负责人(documents.owner_id) 或在分工里被分配了至少一个块。
只列当前用户仍是成员的、未归档项目里的文档。分页，不设总量上限。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.db import connect
from app.errors import AppError
from app.identity import require_session
from app.models import format_timestamp

router = APIRouter()

_STATUS = {"all", "writing", "baselined"}


@router.get("/my-writing")
def my_writing(request: Request, projectId: str | None = None, q: str | None = None,
               offset: int = 0, limit: int = 20) -> dict:
    principal = require_session(request)
    if offset < 0 or not 1 <= limit <= 200:
        raise AppError(422, "VALIDATION_FAILED", "查询范围不正确。")
    uid = principal.user_id
    where = ["""exists (select 1 from project_members m where m.project_id = p.id and m.user_id = %s)""",
             "p.archived_at is null",
             """(d.owner_id = %s or exists (select 1 from document_object_assignments a
                  where a.document_id = d.id and a.assignee_id = %s))"""]
    params: list = [uid, uid, uid]
    if projectId:
        from app.documents import _uuid
        where.append("d.project_id = %s")
        params.append(_uuid(projectId, "projectId"))
    if q and q.strip():
        where.append("d.name like %s")
        params.append(f"%{q.strip()}%")
    with connect() as connection:
        latest = "(select max(b.seq) from document_baselines b where b.document_id = d.id)"
        rows = connection.execute(
            f"""SELECT d.id, d.name, d.status, d.revision, d.updated_at, d.owner_id, d.project_id,
                       p.name AS project_name, ou.name AS owner_name, d.assignment_mode,
                       (select count(*) from document_object_assignments a
                         where a.document_id = d.id and a.assignee_id = %s) AS assigned_count,
                       {latest} AS latest_baseline
                FROM documents d JOIN projects p ON p.id = d.project_id
                LEFT JOIN users ou ON ou.id = d.owner_id
                WHERE {' and '.join(where)}
                ORDER BY d.updated_at DESC, d.name LIMIT %s OFFSET %s""",
            (uid, *params, limit + 1, offset)).fetchall()
        total = connection.execute(
            f"SELECT count(*) AS n FROM documents d JOIN projects p ON p.id = d.project_id WHERE {' and '.join(where)}",
            tuple(params)).fetchone()["n"]
    items = []
    for row in rows[:limit]:
        role = "owner" if str(row["owner_id"]) == str(uid) else "assignee"
        items.append({
            "id": str(row["id"]), "name": row["name"], "status": row["status"], "revision": row["revision"],
            "updatedAt": format_timestamp(row["updated_at"]),
            "project": {"id": str(row["project_id"]), "name": row["project_name"]},
            "owner": {"id": str(row["owner_id"]), "name": row["owner_name"]},
            "myRole": role, "assignmentsEnabled": row["assignment_mode"] == "enforced",
            "assignedBlockCount": row["assigned_count"],
            "latestBaseline": None if row["latest_baseline"] is None else f"B{row['latest_baseline']}",
        })
    return {"data": {"items": items, "offset": offset, "limit": limit, "total": total,
                     "hasMore": len(rows) > limit}, "requestId": request.state.request_id}
