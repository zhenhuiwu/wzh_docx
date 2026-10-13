"""项目基线（30 §3.5，相当于 git tag）：选若干文档，各固定到一个文档版本。

Q-4 默认：基线不冻结文档；之后的修改在清单里显示为“与当前相比 +N 段 / −M 段”。
建立基线只有项目负责人和系统管理员能做；项目成员可读。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect, write_transaction
from app.document_access import document_principal, ensure_project_writable, member_role
from app.document_baselines import _current_sections, _public as version_public, _sections_of, create_baseline, diff_sections
from app.errors import AppError
from app.models import format_timestamp

router = APIRouter()


class ProjectBaselineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    documentIds: list[UUID] = Field(min_length=1)


def _access(connection, request: Request, project_id: str, *, manage: bool = False):
    from app.documents import _admin, _uuid
    actor = document_principal(request)
    pid = _uuid(project_id, "projectId")
    admin = _admin(actor.roles)
    exists = connection.execute("SELECT 1 FROM projects WHERE id=%s", (pid,)).fetchone()
    role = None if admin else member_role(connection, pid, actor.user_id)
    if exists is None or (not admin and role is None):
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
    if manage:
        if not admin and role != "owner":
            raise AppError(403, "FORBIDDEN", "只有项目负责人和管理员可以建立基线。", details={"projectRole": role})
        ensure_project_writable(connection, pid)
    return pid, actor


def _public(row) -> dict:
    return {"id": str(row["id"]), "seq": row["seq"], "label": f"B{row['seq']}", "name": row["name"],
            "createdAt": format_timestamp(row["created_at"]), "documentCount": row["document_count"],
            "author": None if row["author_id"] is None else {"id": str(row["author_id"]), "name": row["author_name"]}}


_SELECT = """SELECT b.*, u.name AS author_name,
             (SELECT count(*) FROM project_baseline_items i WHERE i.project_baseline_id=b.id) AS document_count
             FROM project_baselines b LEFT JOIN users u ON u.id=b.author_id"""


@router.get("/projects/{project_id}/baselines")
def list_project_baselines(request: Request, project_id: str, offset: int = 0, limit: int = 20) -> dict:
    if offset < 0 or not 1 <= limit <= 200:
        raise AppError(422, "VALIDATION_FAILED", "基线查询范围不正确。")
    with connect() as connection:
        pid, _ = _access(connection, request, project_id)
        rows = connection.execute(f"{_SELECT} WHERE b.project_id=%s ORDER BY b.seq DESC LIMIT %s OFFSET %s",
                                  (pid, limit + 1, offset)).fetchall()
        total = connection.execute("SELECT count(*) AS n FROM project_baselines WHERE project_id=%s", (pid,)).fetchone()["n"]
    return {"data": {"items": [_public(r) for r in rows[:limit]], "total": total, "offset": offset, "limit": limit,
                     "hasMore": len(rows) > limit}, "requestId": request.state.request_id}


@router.post("/projects/{project_id}/baselines", status_code=201)
def create_project_baseline(request: Request, project_id: str, body: ProjectBaselineBody) -> dict:
    with connect() as connection:
        pid, actor = _access(connection, request, project_id, manage=True)
        ids = list(dict.fromkeys(body.documentIds))
        found = {str(r["id"]) for r in connection.execute(
            "SELECT id FROM documents WHERE project_id=%s AND id IN (SELECT value FROM json_each(%s))",
            (pid, __import__("json").dumps([str(i) for i in ids]))).fetchall()}
        missing = [str(i) for i in ids if str(i) not in found]
        if missing:
            raise AppError(422, "VALIDATION_FAILED", "有文档不属于这个项目。", details={"documentIds": missing})
        # 验收 40 P2-1：并发建立基线时序号会撞 UNIQUE(project_id, seq) → 500。
        # 序号的读取与写入放进 BEGIN IMMEDIATE 写事务里（开始即持有写锁，并发请求排队），不会再撞号。
        if connection.in_transaction:
            connection.commit()
        with write_transaction(connection):
            seq = connection.execute("SELECT coalesce(max(seq),0)+1 AS n FROM project_baselines WHERE project_id=%s",
                                     (pid,)).fetchone()["n"]
            bid = uuid4()
            connection.execute("INSERT INTO project_baselines(id, project_id, seq, name, author_id) VALUES (%s,%s,%s,%s,%s)",
                               (bid, pid, seq, body.name.strip(), actor.user_id))
            for did in ids:
                latest = connection.execute(
                    """SELECT b.id FROM document_baselines b JOIN documents d ON d.id=b.document_id
                       WHERE b.document_id=%s AND b.revision=d.revision ORDER BY b.seq DESC LIMIT 1""", (did,)).fetchone()
                version_id = latest["id"] if latest else create_baseline(
                    connection, did, actor.user_id, f"基线 B{seq} {body.name.strip()}")["id"]
                connection.execute("INSERT INTO project_baseline_items VALUES (%s,%s,%s)", (bid, did, version_id))
        row = connection.execute(f"{_SELECT} WHERE b.id=%s", (bid,)).fetchone()
    return {"data": _public(row), "requestId": request.state.request_id}


@router.get("/projects/{project_id}/baselines/{seq}")
def get_project_baseline(request: Request, project_id: str, seq: int) -> dict:
    with connect() as connection:
        pid, _ = _access(connection, request, project_id)
        row = connection.execute(f"{_SELECT} WHERE b.project_id=%s AND b.seq=%s", (pid, seq)).fetchone()
        if row is None:
            raise AppError(404, "NOT_FOUND", "找不到这个基线。")
        items = connection.execute(
            """SELECT d.id AS document_id, d.name AS document_name, d.markdown_content AS current_markdown,
                      v.*, u.name AS author_name
               FROM project_baseline_items i JOIN documents d ON d.id=i.document_id
               JOIN document_baselines v ON v.id=i.document_baseline_id LEFT JOIN users u ON u.id=v.author_id
               WHERE i.project_baseline_id=%s ORDER BY d.name""", (row["id"],)).fetchall()
        documents = []
        for item in items:
            _, stats = diff_sections(_sections_of(connection, item["id"]), _current_sections(item["current_markdown"]))
            documents.append({"document": {"id": str(item["document_id"]), "name": item["document_name"]},
                              "version": version_public(item), "sinceBaseline": stats,
                              "changed": any(stats.values())})
    result = _public(row)
    result["documents"] = documents
    return {"data": result, "requestId": request.state.request_id}
