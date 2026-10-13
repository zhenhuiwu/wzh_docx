"""Document-local references pin source revisions and recheck source access on every read."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
import markdown
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.document_access import document_principal, is_admin, load_document, permissions_of
from app.document_assignments import require_scope_write
from app.document_objects import ensure_document_objects, record_commit, revision_conflict
from app.document_versions import record_current_version
from app.errors import AppError

router = APIRouter()


class ReferenceBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=2000)


class DocumentReference(ReferenceBase):
    kind: Literal["document"]
    documentId: str
    sourceRevision: int = Field(ge=0)


class InformationReference(ReferenceBase):
    kind: Literal["information"]
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20000)


Reference = Annotated[DocumentReference | InformationReference, Field(discriminator="kind")]


class ReferencePutBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedRevision: int = Field(ge=0)
    items: list[Reference] = Field(max_length=10)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(422, "VALIDATION_FAILED", "参考资料标识不正确。") from None


def _items(row) -> list[dict]:
    return (row[6] or {}).get("references", [])


def _source(connection, item, principal) -> dict:
    source = load_document(connection, _uuid(item["documentId"]), principal.user_id, is_admin(principal))
    version = connection.execute("SELECT markdown_content FROM document_versions WHERE document_id=%s AND revision=%s",
                                 (source.id, item["sourceRevision"])).fetchone()
    if version is None:
        if source.revision != item["sourceRevision"]:
            raise AppError(409, "REFERENCE_VERSION_MISSING", "参考版本不存在，请重新选择资料版本。")
        content = source.markdown_content
    else:
        content = version["markdown_content"]
    return {**item, "name": source.name, "projectName": source.project_name,
            "latestRevision": source.revision, "content": content, "available": True}


def _editable(connection, row) -> bool:
    enabled = connection.execute("SELECT assignments_enabled FROM documents WHERE id=%s", (row.id,)).fetchone()
    return bool(permissions_of(row.project_role, row.admin, row.archived)["canEdit"] and
                (row.admin or row.project_role == "owner" or not enabled["assignments_enabled"]))


def _state(connection, row, principal) -> dict:
    items = []
    for item in _items(row):
        if item["kind"] == "information":
            items.append({**item, "available": True})
            continue
        try:
            source = _source(connection, item, principal)
        except AppError as error:
            if error.status_code not in {403, 404, 409}:
                raise
            items.append({**item, "name": "参考文档不可用", "available": False})
        else:
            items.append({key: value for key, value in source.items() if key != "content"})
    return {"revision": row.revision, "items": items, "canEdit": _editable(connection, row)}


def writing_references(connection, row, principal) -> list[dict]:
    """Resolve only explicitly attached revisions; never recursively follow their references."""
    result = []
    for item in _items(row):
        if item["kind"] == "information":
            result.append({**item, "available": True})
        else:
            try:
                result.append(_source(connection, item, principal))
            except AppError as error:
                if error.status_code not in {403, 404, 409}:
                    raise
                raise AppError(409, "REFERENCE_UNAVAILABLE", "参考文档已不可用或无权查看，请在参考资料中移除或重新选择后生成。") from None
    return result


def reference_context(references: list[dict], heading: str, instruction: str, max_chars: int = 6000) -> list[dict]:
    """Give every source a bounded original excerpt, with explicit paging instead of silent truncation."""
    limit = max(1, max_chars // max(1, len(references)))
    terms = [re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", heading).strip(),
             *sorted(re.findall(r"[\w\u3400-\u9fff]{2,80}", instruction), key=len, reverse=True)]
    result = []
    for item in references:
        content = item["content"]
        position = next((content.casefold().find(term.casefold()) for term in terms
                         if term and content.casefold().find(term.casefold()) >= 0), 0)
        offset = max(0, position - min(200, limit // 4))
        result.append({key: item.get(key) for key in ("id", "kind", "name", "projectName", "sourceRevision", "note")}
                      | {"content": content[offset:offset+limit], "offset": offset,
                         "hasMore": offset > 0 or offset + limit < len(content), "nextOffset": min(len(content), offset+limit)})
    return result


def _page(source: dict, offset: int, limit: int) -> dict:
    if offset < 0 or not 1 <= limit <= 12000:
        raise AppError(422, "VALIDATION_FAILED", "参考内容读取范围不正确。")
    content = source["content"]
    return {"name": source["name"], "content": content[offset:offset + limit],
            "html": markdown.markdown(content[offset:offset + limit], extensions=["tables"]) if "sourceRevision" in source else "",
            "revision": source.get("sourceRevision"), "hasMore": offset + limit < len(content),
            "nextOffset": min(len(content), offset + limit)}


@router.get("/documents/{document_id}/references")
def list_references(request: Request, document_id: str) -> dict:
    principal = document_principal(request)
    with connect() as connection:
        row = load_document(connection, _uuid(document_id), principal.user_id, is_admin(principal))
        return {"data": _state(connection, row, principal), "requestId": request.state.request_id}


@router.get("/documents/{document_id}/reference-documents")
def reference_documents(request: Request, document_id: str, name: str = "", page: int = 1, pageSize: int = 8) -> dict:
    if not 1 <= page <= 100000 or not 1 <= pageSize <= 50 or len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "参考文档查询范围不正确。")
    principal = document_principal(request)
    with connect() as connection:
        target = load_document(connection, _uuid(document_id), principal.user_id, is_admin(principal))
        where, params = ["d.id <> %s"], [target.id]
        if not is_admin(principal) and "auditor" not in principal.roles:
            where.append("EXISTS (SELECT 1 FROM project_members m WHERE m.project_id=d.project_id AND m.user_id=%s)")
            params.append(principal.user_id)
        if name.strip():
            where.append("(d.name LIKE %s ESCAPE '\\' OR p.name LIKE %s ESCAPE '\\')")
            term = "%" + name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend([term, term])
        condition = " AND ".join(where)
        total = connection.execute(f"SELECT count(*) AS total FROM documents d JOIN projects p ON p.id=d.project_id WHERE {condition}", params).fetchone()["total"]
        rows = connection.execute(f"SELECT d.id,d.name,d.revision,p.name AS project_name FROM documents d JOIN projects p ON p.id=d.project_id "
                                  f"WHERE {condition} ORDER BY d.updated_at DESC,d.id LIMIT %s OFFSET %s", [*params, pageSize, (page-1)*pageSize]).fetchall()
        return {"data": {"items": [{"id": str(row["id"]), "name": row["name"], "projectName": row["project_name"],
                                   "revision": row["revision"]} for row in rows], "total": total, "page": page, "pageSize": pageSize},
                "requestId": request.state.request_id}


@router.get("/documents/{document_id}/reference-documents/{source_id}")
def preview_candidate(request: Request, document_id: str, source_id: str, revision: int, offset: int = 0, limit: int = 12000) -> dict:
    principal = document_principal(request)
    with connect() as connection:
        target = load_document(connection, _uuid(document_id), principal.user_id, is_admin(principal))
        if str(target.id) == str(_uuid(source_id)):
            raise AppError(422, "VALIDATION_FAILED", "当前文档不能作为自己的参考资料。")
        source = _source(connection, {"documentId": source_id, "sourceRevision": revision}, principal)
        return {"data": _page(source, offset, limit), "requestId": request.state.request_id}


@router.get("/documents/{document_id}/references/{reference_id}")
def preview_reference(request: Request, document_id: str, reference_id: str, offset: int = 0, limit: int = 12000) -> dict:
    principal = document_principal(request)
    with connect() as connection:
        target = load_document(connection, _uuid(document_id), principal.user_id, is_admin(principal))
        item = next((item for item in _items(target) if item["id"] == reference_id), None)
        if item is None:
            raise AppError(404, "NOT_FOUND", "参考资料不存在。")
        source = item if item["kind"] == "information" else _source(connection, item, principal)
        return {"data": _page(source, offset, limit), "requestId": request.state.request_id}


@router.put("/documents/{document_id}/references")
def save_references(request: Request, document_id: str, body: ReferencePutBody) -> dict:
    principal = document_principal(request)
    did = _uuid(document_id)
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = load_document(connection, did, principal.user_id, is_admin(principal), "edit")
            require_scope_write(connection, did, principal.user_id)
            if body.expectedRevision != row.revision:
                raise revision_conflict(connection, did, "文档已更新，请刷新后再保存参考资料。")
            items, ids, sources = [], set(), set()
            for model in body.items:
                item = model.model_dump()
                item["id"] = str(_uuid(item["id"]))
                item["note"] = item["note"].strip()
                if item["id"] in ids:
                    raise AppError(422, "VALIDATION_FAILED", "同一参考资料不能重复添加。")
                ids.add(item["id"])
                if item["kind"] == "information":
                    item["name"], item["content"] = item["name"].strip(), item["content"].strip()
                    if not item["name"] or not item["content"]:
                        raise AppError(422, "VALIDATION_FAILED", "参考信息的名称和内容不能为空。")
                else:
                    sid = _uuid(item["documentId"])
                    item["documentId"] = str(sid)
                    if sid == did or sid in sources:
                        raise AppError(422, "VALIDATION_FAILED", "不能重复添加同一文档或引用当前文档。")
                    sources.add(sid)
                    source = load_document(connection, sid, principal.user_id, is_admin(principal))
                    if source.revision == item["sourceRevision"]:
                        record_current_version(connection, sid, expected_revision=source.revision)
                    _source(connection, item, principal)
                items.append(item)
            if items != _items(row):
                ensure_document_objects(connection, did)
                record_current_version(connection, did, expected_revision=row.revision)
                template = {**row.format_template, "references": items}
                updated = connection.execute("UPDATE documents SET format_template=%s,revision=revision+1,updated_at=now() WHERE id=%s AND revision=%s",
                                             (json.dumps(template, ensure_ascii=False), did, row.revision))
                if updated.rowcount != 1:
                    raise revision_conflict(connection, did)
                record_current_version(connection, did, expected_revision=row.revision+1, kind="references")
                record_commit(connection, did, base_revision=row.revision, markdown=None, kind="references")
            row = load_document(connection, did, principal.user_id, is_admin(principal))
            return {"data": _state(connection, row, principal), "requestId": request.state.request_id}
