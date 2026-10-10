"""Template groups and template documents management."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.docx_parser import parse_docx
from app.errors import AppError
from app.files import guard_knowledge_file, resolve_object
from app.identity import require_permission
from app.models import format_timestamp
from app.template_import import parse_template, validate_structure

router = APIRouter()

_STATUSES = {"draft", "published", "retired"}


class TemplateCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: str
    description: str = ""


class TemplatePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    name: str | None = None
    category: str | None = None
    description: str | None = None
    status: str | None = None


class TemplateDocumentCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    displayOrder: int = 0
    wordFileId: str
    importStructure: dict | None = None


class TemplateParseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wordFileId: str


class TemplateStructureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    importStructure: dict


def _word_path(request: Request, file_id: UUID):
    from app.config import get_settings
    guard_knowledge_file(request, str(file_id))
    with connect() as conn:
        row = conn.execute("SELECT storage_key, media_type, status FROM files WHERE id = %s", [file_id]).fetchone()
    if not row or row[2] != "stored":
        raise AppError(404, "NOT_FOUND", "文件不存在。")
    if row[1] != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raise AppError(422, "VALIDATION_FAILED", "请选择 Word .docx 文件。")
    path = resolve_object(get_settings().file_data_dir, row[0])
    if not path.is_file():
        raise AppError(404, "NOT_FOUND", "文件不存在，请重新上传。")
    return path


def _parse_structure(path):
    try:
        return parse_template(path)
    except Exception:
        raise AppError(422, "WORD_PARSE_FAILED", "无法读取 Word 文档，请确认文件有效后重新上传。") from None


def _checked_structure(source: dict, candidate: dict) -> dict:
    try:
        return validate_structure(source, candidate)
    except ValueError as error:
        raise AppError(422, "TEMPLATE_STRUCTURE_INVALID", str(error)) from None


def _document_data(row) -> dict:
    format_template = row[5] if isinstance(row[5], dict) else {}
    return {"id": str(row[0]), "name": row[1], "description": row[2], "displayOrder": row[3],
            "wordFileId": str(row[4]), "formatTemplate": format_template,
            "importStructure": format_template.get("importStructure"),
            "createdAt": format_timestamp(row[6]), "updatedAt": format_timestamp(row[7])}


def _uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "标识不正确。",
            [{"field": field, "message": "需要有效标识。"}],
        ) from None


def _admin(roles: tuple[str, ...]) -> bool:
    return "system_admin" in roles


@router.get("/templates")
def list_templates(request: Request, page: int = 1, pageSize: int = 20, name: str = "") -> dict[str, object]:
    """列出所有模板组"""
    require_permission(request, "templates.read")
    if page < 1 or not 1 <= pageSize <= 100:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    page_size = pageSize
    name_query = name.strip()

    offset = (page - 1) * page_size

    with connect() as conn:
        where = []
        params: list[object] = []

        if name_query:
            where.append("t.name ILIKE %s ESCAPE '\\'")
            escaped = name_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        # 获取总数
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM templates t {where_clause}",
            params
        ).fetchone()
        total = count_row[0] if count_row else 0

        # 获取列表
        params.extend([page_size, offset])
        rows = conn.execute(
            f"""
            SELECT t.id, t.name, t.category, t.description, t.status, t.revision,
                   t.updated_at,
                   (SELECT COUNT(*) FROM template_documents td WHERE td.template_id = t.id) as doc_count
            FROM templates t
            {where_clause}
            ORDER BY t.updated_at DESC, t.name ASC
            LIMIT %s OFFSET %s
            """,
            params
        ).fetchall()

        items = [
            {
                "id": str(row[0]),
                "name": row[1],
                "category": row[2],
                "description": row[3],
                "status": row[4],
                "revision": row[5],
                "updatedAt": format_timestamp(row[6]),
                "documentCount": row[7],
            }
            for row in rows
        ]

        return {
            "data": {
                "items": items,
                "page": page,
                "pageSize": page_size,
                "total": total,
            },
            "requestId": request.state.request_id,
        }


@router.post("/templates")
def create_template(request: Request, body: TemplateCreateBody) -> dict[str, object]:
    """创建模板组"""
    require_permission(request, "templates.manage")

    name = body.name.strip()
    category = body.category.strip()
    description = body.description.strip()

    if not name or len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "名称长度应在 1-120 字符之间。")

    if not category or len(category) > 120:
        raise AppError(422, "VALIDATION_FAILED", "分类长度应在 1-120 字符之间。")

    template_id = uuid4()

    with connect() as conn:
        # 检查重复
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM templates WHERE name = %s",
            [name]
        ).fetchone()

        if existing:
            raise AppError(409, "DUPLICATE_NAME", "已存在同名模板组。")

        # 创建模板组
        conn.execute(
            """
            INSERT INTO templates (id, name, category, description, status, revision)
            VALUES (%s, %s, %s, %s, 'draft', 0)
            """,
            [template_id, name, category, description]
        )
        conn.commit()

        # 返回创建的记录
        row = conn.execute(
            """
            SELECT id, name, category, description, status, revision, updated_at
            FROM templates WHERE id = %s
            """,
            [template_id]
        ).fetchone()

        return {
            "data": {
                "id": str(row[0]),
                "name": row[1],
                "category": row[2],
                "description": row[3],
                "status": row[4],
                "revision": row[5],
                "updatedAt": format_timestamp(row[6]),
                "documentCount": 0,
            },
            "requestId": request.state.request_id,
        }


@router.get("/templates/{template_id}")
def get_template(request: Request, template_id: str) -> dict[str, object]:
    """获取模板组详情"""
    require_permission(request, "templates.read")
    tid = _uuid(template_id, "templateId")

    with connect() as conn:
        row = conn.execute(
            """
            SELECT t.id, t.name, t.category, t.description, t.status, t.revision, t.updated_at,
                   (SELECT COUNT(*) FROM template_documents td WHERE td.template_id = t.id) as doc_count
            FROM templates t
            WHERE t.id = %s
            """,
            [tid]
        ).fetchone()

        if not row:
            raise AppError(404, "NOT_FOUND", "模板组不存在。")

        return {
            "data": {
                "id": str(row[0]),
                "name": row[1],
                "category": row[2],
                "description": row[3],
                "status": row[4],
                "revision": row[5],
                "updatedAt": format_timestamp(row[6]),
                "documentCount": row[7],
            },
            "requestId": request.state.request_id,
        }


@router.patch("/templates/{template_id}")
def update_template(
    request: Request,
    template_id: str,
    body: TemplatePatchBody
) -> dict[str, object]:
    """更新模板组"""
    require_permission(request, "templates.manage")
    tid = _uuid(template_id, "templateId")

    with connect() as conn:
        # 检查存在和版本
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT revision FROM templates WHERE id = %s",
            [tid]
        ).fetchone()

        if not row:
            raise AppError(404, "NOT_FOUND", "模板组不存在。")

        if row[0] != body.expectedRevision:
            raise AppError(409, "REVISION_CONFLICT", "版本冲突，请刷新后重试。")

        # 构建更新语句
        updates = []
        params: list[object] = []

        if body.name is not None:
            name = body.name.strip()
            if not name or len(name) > 120:
                raise AppError(422, "VALIDATION_FAILED", "名称长度应在 1-120 字符之间。")
            if conn.execute("SELECT id FROM templates WHERE name = %s AND id <> %s", [name, tid]).fetchone():
                raise AppError(409, "DUPLICATE_NAME", "已存在同名模板组。")
            updates.append("name = %s")
            params.append(name)

        if body.category is not None:
            category = body.category.strip()
            if not category or len(category) > 120:
                raise AppError(422, "VALIDATION_FAILED", "分类长度应在 1-120 字符之间。")
            updates.append("category = %s")
            params.append(category)

        if body.description is not None:
            updates.append("description = %s")
            params.append(body.description.strip())

        if body.status is not None:
            if body.status not in _STATUSES:
                raise AppError(422, "VALIDATION_FAILED", "状态值无效。")
            updates.append("status = %s")
            params.append(body.status)

        if not updates:
            raise AppError(422, "VALIDATION_FAILED", "没有可更新的字段。")

        updates.append("revision = revision + 1")
        updates.append("updated_at = now()")

        params.extend([tid, body.expectedRevision])

        conn.execute(
            f"UPDATE templates SET {', '.join(updates)} WHERE id = %s AND revision = %s",
            params
        )
        conn.commit()

        # 返回更新后的记录
        row = conn.execute(
            """
            SELECT id, name, category, description, status, revision, updated_at
            FROM templates WHERE id = %s
            """,
            [tid]
        ).fetchone()

        return {
            "data": {
                "id": str(row[0]),
                "name": row[1],
                "category": row[2],
                "description": row[3],
                "status": row[4],
                "revision": row[5],
                "updatedAt": format_timestamp(row[6]),
            },
            "requestId": request.state.request_id,
        }


@router.get("/templates/{template_id}/documents")
def list_template_documents(request: Request, template_id: str) -> dict[str, object]:
    """列出模板组中的文档"""
    require_permission(request, "templates.read")
    tid = _uuid(template_id, "templateId")

    with connect() as conn:
        # 检查模板组存在
        template = conn.execute(
            "SELECT id FROM templates WHERE id = %s",
            [tid]
        ).fetchone()

        if not template:
            raise AppError(404, "NOT_FOUND", "模板组不存在。")

        # 获取文档列表
        rows = conn.execute(
            """
            SELECT td.id, td.name, td.description, td.display_order, td.word_file_id,
                   td.format_template, td.created_at, td.updated_at
            FROM template_documents td
            WHERE td.template_id = %s
            ORDER BY td.display_order, td.name
            """,
            [tid]
        ).fetchall()

        items = [_document_data(row) for row in rows]

        return {
            "data": {"items": items},
            "requestId": request.state.request_id,
        }


@router.post("/templates/{template_id}/documents")
def create_template_document(
    request: Request,
    template_id: str,
    body: TemplateDocumentCreateBody
) -> dict[str, object]:
    """添加文档到模板组（上传 Word 并解析）"""
    require_permission(request, "templates.manage")
    tid = _uuid(template_id, "templateId")
    word_file_id = _uuid(body.wordFileId, "wordFileId")

    name = body.name.strip()
    if not name or len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "文档名称长度应在 1-120 字符之间。")

    guard_knowledge_file(request, str(word_file_id))

    with connect() as conn:
        # 检查模板组存在
        template = conn.execute(
            "SELECT id FROM templates WHERE id = %s",
            [tid]
        ).fetchone()

        if not template:
            raise AppError(404, "NOT_FOUND", "模板组不存在。")

        # 检查文件存在
        file_row = conn.execute(
            "SELECT id, storage_key, media_type, status FROM files WHERE id = %s",
            [word_file_id]
        ).fetchone()

        if not file_row or file_row[3] != "stored":
            raise AppError(404, "NOT_FOUND", "文件不存在。")
        if file_row[2] != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            raise AppError(422, "VALIDATION_FAILED", "请选择 Word .docx 文件。")

        storage_key = file_row[1]

        # 解析 Word 文档
        from app.config import get_settings
        file_path = resolve_object(get_settings().file_data_dir, storage_key)
        if not file_path.is_file():
            raise AppError(404, "NOT_FOUND", "文件不存在，请重新上传。")
        try:
            parsed = parse_docx(str(file_path), outline_headings=True)
            structure = _parse_structure(file_path)
            if body.importStructure is not None:
                structure = _checked_structure(structure, body.importStructure)
            format_template = {**parsed.format_template, "importStructure": structure}
        except AppError:
            raise
        except Exception:
            raise AppError(422, "WORD_PARSE_FAILED", "无法读取 Word 文档，请确认文件有效后重新上传。") from None

        # 检查重复
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM template_documents WHERE template_id = %s AND name = %s",
            [tid, name]
        ).fetchone()

        if existing:
            raise AppError(409, "DUPLICATE_NAME", "模板组中已存在同名文档。")

        # 创建模板文档
        doc_id = uuid4()
        conn.execute(
            """
            INSERT INTO template_documents
            (id, template_id, name, description, display_order, word_file_id, format_template)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [doc_id, tid, name, body.description, body.displayOrder, word_file_id, json.dumps(format_template)]
        )
        conn.commit()

        # 返回创建的记录
        row = conn.execute(
            """
            SELECT id, name, description, display_order, word_file_id, format_template, created_at, updated_at
            FROM template_documents WHERE id = %s
            """,
            [doc_id]
        ).fetchone()

        return {
            "data": _document_data(row),
            "requestId": request.state.request_id,
        }


@router.post("/template-documents/parse")
def parse_template_document(request: Request, body: TemplateParseBody) -> dict:
    require_permission(request, "templates.manage")
    path = _word_path(request, _uuid(body.wordFileId, "wordFileId"))
    return {"data": _parse_structure(path), "requestId": request.state.request_id}


@router.get("/template-documents/{document_id}")
def get_template_document(request: Request, document_id: str) -> dict:
    require_permission(request, "templates.read")
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        row = conn.execute("""SELECT id, name, description, display_order, word_file_id,
                              format_template, created_at, updated_at FROM template_documents WHERE id=%s""", [did]).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "模板文档不存在。")
    data = _document_data(row)
    if data["importStructure"] is None:
        data["importStructure"] = _parse_structure(_word_path(request, row[4]))
    return {"data": data, "requestId": request.state.request_id}


@router.patch("/template-documents/{document_id}/structure")
def update_template_structure(request: Request, document_id: str, body: TemplateStructureBody) -> dict:
    require_permission(request, "templates.manage")
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT id, name, description, display_order, word_file_id,
                              format_template, created_at, updated_at FROM template_documents WHERE id=%s""", [did]).fetchone()
        if row is None:
            raise AppError(404, "NOT_FOUND", "模板文档不存在。")
        format_template = dict(row[5] or {})
        saved = format_template.get("importStructure") or {"revision": 0}
        if saved["revision"] != body.expectedRevision:
            raise AppError(409, "REVISION_CONFLICT", "模板结构已更新，请重新读取后修改。")
        source = _parse_structure(_word_path(request, row[4]))
        source["revision"] = saved["revision"]
        checked = _checked_structure(source, body.importStructure)
        checked["revision"] += 1
        format_template["importStructure"] = checked
        conn.execute("UPDATE template_documents SET format_template=%s, updated_at=now() WHERE id=%s", [json.dumps(format_template), did])
        conn.commit()
        row = conn.execute("""SELECT id, name, description, display_order, word_file_id,
                              format_template, created_at, updated_at FROM template_documents WHERE id=%s""", [did]).fetchone()
    return {"data": _document_data(row), "requestId": request.state.request_id}


@router.delete("/template-documents/{document_id}")
def delete_template_document(request: Request, document_id: str) -> dict[str, object]:
    """从模板组中删除文档"""
    require_permission(request, "templates.manage")
    doc_id = _uuid(document_id, "documentId")

    with connect() as conn:
        # 检查文档存在
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM template_documents WHERE id = %s",
            [doc_id]
        ).fetchone()

        if not row:
            raise AppError(404, "NOT_FOUND", "模板文档不存在。")

        # 检查是否有项目文档引用
        usage = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE template_document_id = %s",
            [doc_id]
        ).fetchone()

        if usage and usage[0] > 0:
            raise AppError(409, "IN_USE", f"该模板文档已被 {usage[0]} 个项目文档使用，无法删除。")

        # 删除
        conn.execute("DELETE FROM template_documents WHERE id = %s", [doc_id])
        conn.commit()

        return {
            "data": {"deleted": True},
            "requestId": request.state.request_id,
        }
