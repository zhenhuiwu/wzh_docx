"""Persistent document snapshots and CAS restoration, including adopted memory state."""

from __future__ import annotations

import json
from uuid import UUID

import markdown
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.errors import AppError
from app.document_objects import revision_conflict
from app.document_access import document_principal
from app.models import format_timestamp

router = APIRouter()


class VersionRestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)


def _active_revisions(connection, document_id: UUID, revision: int) -> str:
    row = connection.execute(
        """SELECT json_group_array(revision) AS revisions FROM (
           SELECT revision FROM document_memory_events e WHERE document_id=%s AND revision<=%s
           AND NOT EXISTS (SELECT 1 FROM document_memory_retractions r
                           WHERE r.document_id=e.document_id AND r.revision=e.revision)
           ORDER BY revision)""", (document_id, revision),
    ).fetchone()
    return row["revisions"]


def record_current_version(connection, document_id: UUID, *, expected_revision: int | None = None,
                           kind: str = "baseline", restored_from_revision: int | None = None):
    """Capture a revision once; the caller owns the transaction and commit."""
    row = connection.execute(
        "SELECT revision, markdown_content, outline, format_template, status, updated_at FROM documents WHERE id=%s",
        (document_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个文档。")
    if expected_revision is not None and row["revision"] != expected_revision:
        raise revision_conflict(connection, document_id, "文档已更新，请刷新后重试。")
    if connection.execute("SELECT 1 FROM document_versions WHERE document_id=%s AND revision=%s",
                          (document_id, row["revision"])).fetchone():
        return row
    connection.execute(
        """INSERT INTO document_versions
           (document_id, revision, markdown_content, outline, format_template, memory_revisions,
            status, kind, restored_from_revision, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(document_id, revision) DO NOTHING""",
        (document_id, row["revision"], row["markdown_content"], json.dumps(row["outline"], ensure_ascii=False),
         json.dumps(row["format_template"], ensure_ascii=False), _active_revisions(connection, document_id, row["revision"]),
         row["status"], kind, restored_from_revision, row["updated_at"]),
    )
    return row


def sync_current_version_memory(connection, document_id: UUID) -> None:
    """Finalize new-revision memory after adoption/retraction, before the save commits."""
    row = connection.execute("""SELECT d.revision FROM documents d JOIN document_versions v
                                ON v.document_id=d.id AND v.revision=d.revision WHERE d.id=%s""", (document_id,)).fetchone()
    if row is not None:
        connection.execute("UPDATE document_versions SET memory_revisions=%s WHERE document_id=%s AND revision=%s",
                           (_active_revisions(connection, document_id, row["revision"]), document_id, row["revision"]))


def _public(row, *, detail: bool = False) -> dict:
    value = {"revision": row["revision"], "createdAt": format_timestamp(row["created_at"]),
             "kind": row["kind"], "restoredFromRevision": row["restored_from_revision"]}
    # 112 之前的历史版本两项为 null，界面显示“未记录”（4.5）
    names = list(row)
    created_by = row["created_by"] if "created_by" in names else None
    value["createdBy"] = None if created_by is None else {
        "id": str(created_by), "name": row["created_by_name"] if "created_by_name" in names else None}
    value["changedObjects"] = row["changed_objects"] if "changed_objects" in names else None
    if detail:
        value.update(markdownContent=row["markdown_content"], outline=row["outline"], formatTemplate=row["format_template"],
                     html=markdown.markdown(row["markdown_content"], extensions=["tables", "fenced_code"]))
    return value


def _visible(connection, request: Request, document_id: str, permission: str):
    from app.documents import _admin, _document_row, _uuid
    actor = document_principal(request)
    did = _uuid(document_id, "documentId")
    need = "edit" if permission == "documents.edit" else "read"
    return did, _document_row(connection, did, actor.user_id, _admin(actor.roles), need)


def _version(connection, document_id: UUID, revision: int):
    if revision < 0:
        raise AppError(422, "VALIDATION_FAILED", "历史版本不正确。")
    row = connection.execute("SELECT * FROM document_versions WHERE document_id=%s AND revision=%s", (document_id, revision)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "找不到这个历史版本。")
    return row


@router.get("/documents/{document_id}/versions")
def list_versions(request: Request, document_id: str, offset: int = 0, limit: int = 10) -> dict:
    if offset < 0 or not 1 <= limit <= 50:
        raise AppError(422, "VALIDATION_FAILED", "版本查询范围不正确。")
    with connect() as connection:
        did, current = _visible(connection, request, document_id, "documents.read")
        record_current_version(connection, did)
        rows = connection.execute("""SELECT v.revision, v.created_at, v.kind, v.restored_from_revision, v.created_by,
                                     u.name AS created_by_name, v.changed_objects
                                     FROM document_versions v LEFT JOIN users u ON u.id = v.created_by
                                     WHERE v.document_id=%s ORDER BY v.revision DESC LIMIT %s OFFSET %s""",
                                  (did, limit + 1, offset)).fetchall()
        connection.commit()
    return {"data": {"items": [_public(row) for row in rows[:limit]], "offset": offset, "limit": limit,
                     "hasMore": len(rows) > limit, "currentRevision": current[12]}, "requestId": request.state.request_id}


@router.get("/documents/{document_id}/versions/{revision}")
def get_version(request: Request, document_id: str, revision: int) -> dict:
    from app.documents import _editing_html
    with connect() as connection:
        did, current = _visible(connection, request, document_id, "documents.read")
        record_current_version(connection, did)
        version = _version(connection, did, revision)
        snapshot = [current[index] for index in range(16)]
        snapshot[6], snapshot[7] = version["format_template"], version["markdown_content"]
        result = _public(version, detail=True)
        result["html"] = _editing_html(connection, snapshot)
        connection.commit()
    return {"data": result, "requestId": request.state.request_id}


def _restore_memory(connection, document_id: UUID, memory_revisions: str) -> None:
    # SQLite JSON tables avoid an unbounded number of SQL parameters. Original
    # events remain present; restoring a version can reactivate its old requests.
    connection.execute(
        """INSERT INTO document_memory_retractions(document_id, revision)
           SELECT document_id, revision FROM document_memory_events
           WHERE document_id=%s AND revision NOT IN (SELECT value FROM json_each(%s))
           ON CONFLICT(document_id,revision) DO NOTHING""", (document_id, memory_revisions),
    )
    connection.execute("DELETE FROM document_memory_retractions WHERE document_id=%s AND revision IN (SELECT value FROM json_each(%s))",
                       (document_id, memory_revisions))
    from app.document_memory import _refresh_summary
    _refresh_summary(connection, document_id)


@router.post("/documents/{document_id}/versions/{revision}/restore")
def restore_version(request: Request, document_id: str, revision: int, body: VersionRestoreBody) -> dict:
    from app.documents import _check_word, _content_result, _document_row, _admin, _save_markdown
    with connect() as connection:
        did, current = _visible(connection, request, document_id, "documents.edit")
        if current[12] != body.expectedRevision:
            raise revision_conflict(connection, did, "文档已更新，请重新核对后恢复。")
        record_current_version(connection, did, expected_revision=body.expectedRevision)
        version = _version(connection, did, revision)
        template = version["format_template"]
        _check_word(connection, current, version["markdown_content"], images=template.get("images", []))
        _save_markdown(connection, did, version["markdown_content"], body.expectedRevision,
                       format_template=template, outline=version["outline"], kind="restore", restored_from_revision=revision)
        _restore_memory(connection, did, version["memory_revisions"])
        connection.commit()
        actor = document_principal(request)
        current = _document_row(connection, did, actor.user_id, _admin(actor.roles))
        result = _content_result(request, current)
        result["data"]["formatTemplate"] = current[6]
        return result
