"""Document instances with Markdown editing."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import sys
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic
from html import escape
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db import connect
from app.document_agent import draft_section_text, stream_section_text
from app.document_rules import DraftFormatError, section_rules, validate_draft, validate_constraints
from app.document_memory import clear_memory, load_memory, record_memory, read_memory_history, retract_memory
from app.document_versions import record_current_version, router as versions_router
from app.docx_parser import parse_docx
from app.ai_capability import require_ai
from app.errors import AppError
from app.config import get_settings
from app.files import resolve_object, store_bytes
from app.template_import import clean_template
from app.document_objects import ensure_document_objects, record_commit, revision_conflict, router as objects_router
from app.document_access import DocumentRow, Need, check_project, document_principal, load_document, member_role, permissions_of
from app.models import format_timestamp
from app.writing_skills import load_skill_snapshot

router = APIRouter()
router.include_router(versions_router)
router.include_router(objects_router)
# S3（01 §5.5）：Word COM 仍串行，但只包住 COM 调用；同一预览目标按文件键加锁只生成一次；
# 缓存命中与 edit_original_docx 都在锁外。进程内锁只对单进程部署有效。
_COM_SLOT = BoundedSemaphore(1)
WORD_SLOT_TIMEOUT_SECONDS = 120.0
_target_locks: dict[str, Lock] = {}
_target_locks_guard = Lock()


@contextmanager
def _word_slot():
    if not _COM_SLOT.acquire(timeout=WORD_SLOT_TIMEOUT_SECONDS):
        raise AppError(503, "WORD_BUSY", "Word 正在处理其他文档，请稍后重试。", retry_after=5)
    try:
        yield
    finally:
        _COM_SLOT.release()


def _target_lock(key: str) -> Lock:
    with _target_locks_guard:
        return _target_locks.setdefault(key, Lock())


def _word_available() -> bool:
    return sys.platform == "win32"


@contextmanager
def _local_word_document(path: Path, *, read_only: bool = True):
    if sys.platform != "win32":
        raise AppError(503, "WORD_UNAVAILABLE", "完整目录和版式处理需要在本机安装 Microsoft Word。")
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        try:
            word.Visible = False
            word.DisplayAlerts = 0
            word.AutomationSecurity = 3
            opened = word.Documents.Open(str(path), ReadOnly=read_only, AddToRecentFiles=False)
            try:
                yield opened
            finally:
                opened.Close(False)
        finally:
            word.Quit()
    finally:
        pythoncom.CoUninitialize()


def _refresh_word_contents(opened) -> None:
    # Refresh only local indexes, never unrelated external or computed fields.
    indexes = [*opened.TablesOfContents, *opened.TablesOfFigures]
    if not indexes:
        return
    for index in indexes:
        index.Update()
    opened.Repaginate()
    for index in indexes:
        index.UpdatePageNumbers()


def _has_word_contents(path: Path) -> bool:
    from docx import Document
    values = Document(path).element.xpath(".//w:instrText/text()|.//w:fldSimple/@w:instr")
    return any(re.match(r"^\s*TOC\b", value, re.IGNORECASE) for value in values)

_STATUSES = {"draft", "in_progress", "completed"}
_SOURCE_TYPES = {"template", "upload"}


class DocumentCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    sourceType: str
    templateDocumentId: str | None = None
    wordFileId: str | None = None


class WritingMemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(max_length=2000)
    scope: Literal["section", "selection", "document"]
    target: str = Field(max_length=120, pattern=r"^[^\r\n]*$")


class DocumentContentPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    markdownContent: str
    memory: WritingMemoryBody | None = None
    retractMemoryRevision: int | None = Field(default=None, ge=0)


class DocumentExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)


class DocumentSkillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    mode: Literal["auto", "none", "selected"]
    skillId: str | None = None
    skillRevision: int | None = Field(default=None, ge=0)
    documentInstructions: str = Field(default="", max_length=2000)


class HeadingAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    afterIndex: int | None = Field(default=None, ge=0)
    placement: str = "child"
    title: str


class DocumentSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    blockId: str = Field(min_length=1, max_length=120)
    endBlockId: str | None = Field(default=None, min_length=1, max_length=120)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=8000)


class SelectionInsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    selection: DocumentSelectionBody
    content: str = Field(min_length=1, max_length=20000)
    instruction: str = Field(default="", max_length=2000)


class SectionDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    headingIndex: int | None = Field(default=None, ge=0)
    selectedText: str | None = Field(default=None, min_length=1, max_length=8000)
    instruction: str = Field(default="", max_length=2000)
    scope: Literal["section", "selection", "document"] = "section"
    selection: DocumentSelectionBody | None = None


class SectionInsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    headingIndex: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=20000)
    mode: Literal["append", "replace"] = "append"
    instruction: str = Field(default="", max_length=2000)


class DocumentEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    headingIndex: int | None = Field(ge=0)
    content: str = Field(min_length=1, max_length=20000)


class DocumentInsertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    edits: list[DocumentEdit] = Field(min_length=1, max_length=200)
    instruction: str = Field(default="", max_length=2000)


_HEADING = re.compile(r"^(#{1,6})[ \t]+([^\n]+)$", re.MULTILINE)


def _headings(markdown: str) -> list[dict[str, object]]:
    return [
        {"level": len(match.group(1)), "text": match.group(2).strip(), "start": match.start(), "bodyStart": match.end()}
        for match in _HEADING.finditer(markdown)
    ]


def _heading(markdown: str, index: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    headings = _headings(markdown)
    if index >= len(headings):
        raise AppError(422, "VALIDATION_FAILED", "所选标题已不存在，请刷新文档。")
    return headings, headings[index]


def _outline_from_markdown(markdown: str) -> list[dict[str, object]]:
    roots: list[dict[str, object]] = []
    stack: list[dict[str, object]] = []
    for index, item in enumerate(_headings(markdown)):
        node: dict[str, object] = {"id": f"heading-{index}", "level": item["level"], "text": item["text"], "children": []}
        while stack and int(stack[-1]["level"]) >= int(item["level"]):
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(node)
        stack.append(node)
    return roots


def _insert_at(markdown: str, offset: int, text: str) -> str:
    before = markdown[:offset].rstrip("\n")
    after = markdown[offset:].lstrip("\n")
    return (before + "\n\n" if before else "") + text.strip("\n") + ("\n\n" + after if after else "")


_AI_KINDS = frozenset({"selection", "ai_section", "ai_document", "diagram"})


def _save_markdown(connection, document_id: UUID, markdown: str, expected_revision: int, *,
                   format_template: dict | None = None, outline: list | None = None,
                   kind: str = "edit", restored_from_revision: int | None = None) -> UUID:
    ensure_document_objects(connection, document_id)
    before = record_current_version(connection, document_id, expected_revision=expected_revision)
    template = before["format_template"] if format_template is None else format_template
    updated = connection.execute(
        """UPDATE documents SET markdown_content = %s, outline = %s, format_template = %s,
           status = 'in_progress', revision = revision + 1, updated_at = now() WHERE id = %s AND revision = %s""",
        [markdown, json.dumps(_outline_from_markdown(markdown) if outline is None else outline, ensure_ascii=False),
         json.dumps(template, ensure_ascii=False), document_id, expected_revision],
    )
    if updated.rowcount != 1:
        raise revision_conflict(connection, document_id)
    record_current_version(connection, document_id, expected_revision=expected_revision + 1,
                           kind=kind, restored_from_revision=restored_from_revision)
    return record_commit(connection, document_id, base_revision=expected_revision, markdown=markdown, kind=kind,
                         ai_assisted=kind in _AI_KINDS)


def _check_word(connection, row, content: str, *, images: list[dict] | None = None) -> None:
    """Every save must remain exportable using the original Word package."""
    from tempfile import TemporaryDirectory
    from app.docx_export import edit_original_docx, diagram_images
    file_row = connection.execute("SELECT storage_key FROM files WHERE id = %s AND status = 'stored'", [row[5]]).fetchone()
    if file_row is None:
        raise AppError(404, "NOT_FOUND", "原始 Word 文件不存在。")
    try:
        with TemporaryDirectory() as directory:
            edit_original_docx(content, resolve_object(get_settings().file_data_dir, file_row[0]), Path(directory) / "checked.docx",
                               image_sources=diagram_images(images if images is not None else row[6].get("images", [])),
                               outline_headings=bool(row[6].get("wordOutlineHeadings")))
    except ValueError as exc:
        raise word_format_error(content, exc) from None


def word_format_error(content: str, exc: ValueError) -> AppError:
    """422 AI_FORMAT_INVALID with the failing block's location (01 §4.10)."""
    from app.docx_export import _html_blocks
    from app.document_location import compute_locations, html_block_items, location_text
    index = getattr(exc, "block_index", None)
    if index is None:
        return AppError(422, "AI_FORMAT_INVALID", str(exc))
    try:
        location = compute_locations(html_block_items(_html_blocks(content)))[index]
    except (IndexError, ValueError):
        return AppError(422, "AI_FORMAT_INVALID", str(exc))
    return AppError(422, "AI_FORMAT_INVALID", f"{location_text(location)}：{exc}", details={"location": location})


def _content_result(request: Request, row: dict) -> dict[str, object]:
    return {"data": {"id": str(row[0]), "markdownContent": row[7], "outline": row[8],
                     "status": row[9], "revision": row[12], "updatedAt": format_timestamp(row[13])},
            "requestId": request.state.request_id}


def _writing_skill(row) -> dict | None:
    return row[6].get("writingSkill") if isinstance(row[6], dict) else None


@router.patch("/documents/{document_id}/writing-skill")
def set_document_skill(request: Request, document_id: str, body: DocumentSkillBody) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        with conn.transaction(immediate=True):
            row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
            if row[12] != body.expectedRevision:
                raise revision_conflict(conn, did, "文档已更新，请关闭设置并刷新后重试。")
            current = _writing_skill(row) or {"mode": "auto", "documentInstructions": ""}
            if body.mode == "selected":
                if not body.skillId or body.skillRevision is None:
                    raise AppError(422, "VALIDATION_FAILED", "请选择编写规范。")
                sid = str(_uuid(body.skillId, "skillId"))
                if current.get("mode") == "selected" and current.get("id") == sid and current.get("revision") == body.skillRevision:
                    # A pinned version stays usable after global edits or disabling.
                    snapshot = dict(current)
                else:
                    snapshot = load_skill_snapshot(sid, connection=conn)
                    if snapshot["revision"] != body.skillRevision:
                        raise AppError(409, "SKILL_REVISION_CONFLICT", "Skill 已更新，请关闭设置后重新选择。")
                    snapshot["mode"] = "selected"
            else:
                if body.skillId is not None or body.skillRevision is not None:
                    raise AppError(422, "VALIDATION_FAILED", "自动匹配或不使用 Skill 时不能指定技能。")
                snapshot = {"mode": body.mode}
            snapshot["documentInstructions"] = body.documentInstructions.strip()
            if snapshot != current:
                ensure_document_objects(conn, did)
                record_current_version(conn, did, expected_revision=row[12])
                template = {**row[6], "writingSkill": snapshot}
                updated = conn.execute(
                    """UPDATE documents SET format_template=%s, revision=revision+1, updated_at=now()
                       WHERE id=%s AND revision=%s""",
                    [json.dumps(template, ensure_ascii=False), did, row[12]],
                )
                if updated.rowcount != 1:
                    raise revision_conflict(conn, did)
                record_current_version(conn, did, expected_revision=row[12]+1, kind="skill_settings")
                record_commit(conn, did, base_revision=row[12], markdown=None, kind="skill_settings")
            row = _document_row(conn, did, session.user_id, _admin(session.roles))
            result = _content_result(request, row)
            result["data"]["formatTemplate"] = row[6]
            return result


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


def _project_visible(connection, project_id: UUID, user_id: UUID, admin: bool, need: Need = "read") -> str | None:
    """项目成员可见（S1）；非成员 404，不区分项目是否存在。返回项目角色。"""
    exists = connection.execute("SELECT id FROM projects WHERE id = %s", [project_id]).fetchone()
    if exists is None:
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。")
    role = member_role(connection, project_id, user_id)
    check_project(connection, project_id, role, admin, need)
    return role


def _document_row(connection, document_id: UUID, user_id: UUID, admin: bool, need: Need = "read") -> DocumentRow:
    """按名可取列的文档行（DocumentRow）；可见性与权限按项目成员角色（S1）。"""
    return load_document(connection, document_id, user_id, admin, need)


@router.get("/projects/{project_id}/documents")
def list_documents(request: Request, project_id: str) -> dict[str, object]:
    """列出项目文档"""
    session = document_principal(request)
    admin = _admin(session.roles)
    pid = _uuid(project_id, "projectId")

    page = int(request.query_params.get("page", "1"))
    page_size = min(int(request.query_params.get("pageSize", "20")), 100)
    name_query = request.query_params.get("name", "").strip()

    offset = (page - 1) * page_size

    with connect() as conn:
        role = _project_visible(conn, pid, session.user_id, admin)
        permissions = permissions_of(role, admin)

        where = ["d.project_id = %s"]
        params: list[object] = [pid]

        if name_query:
            where.append("d.name ILIKE %s")
            params.append(f"%{name_query}%")

        where_clause = f"WHERE {' AND '.join(where)}"

        # 获取总数
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM documents d {where_clause}",
            params
        ).fetchone()
        total = count_row[0] if count_row else 0

        # 获取列表
        params.extend([page_size, offset])
        rows = conn.execute(
            f"""
            SELECT d.id, d.name, d.source_type, d.status, d.owner_id, owner.name as owner_name,
                   d.revision, d.updated_at, td.name as template_document_name
            FROM documents d
            JOIN users owner ON owner.id = d.owner_id
            LEFT JOIN template_documents td ON td.id = d.template_document_id
            {where_clause}
            ORDER BY d.updated_at DESC, d.name ASC
            LIMIT %s OFFSET %s
            """,
            params
        ).fetchall()

        items = [
            {
                "id": str(row[0]),
                "name": row[1],
                "sourceType": row[2],
                "status": row[3],
                "ownerId": str(row[4]),
                "ownerName": row[5],
                "revision": row[6],
                "updatedAt": format_timestamp(row[7]),
                "templateDocumentName": row[8],
                "permissions": permissions,
            }
            for row in rows
        ]

        return {
            "data": {
                "items": items,
                "permissions": permissions,
                "page": page,
                "pageSize": page_size,
                "total": total,
            },
            "requestId": request.state.request_id,
        }


@router.post("/projects/{project_id}/documents")
def create_document(
    request: Request,
    project_id: str,
    body: DocumentCreateBody
) -> dict[str, object]:
    """创建文档（从模板或上传）"""
    session = document_principal(request)
    admin = _admin(session.roles)
    pid = _uuid(project_id, "projectId")

    name = body.name.strip()
    if not name or len(name) > 120:
        raise AppError(422, "VALIDATION_FAILED", "文档名称长度应在 1-120 字符之间。")

    if body.sourceType not in _SOURCE_TYPES:
        raise AppError(422, "VALIDATION_FAILED", "来源类型无效。")

    with connect() as conn:
        _project_visible(conn, pid, session.user_id, admin, "edit")

        # 检查重复
        existing = conn.execute(
            "SELECT id FROM documents WHERE project_id = %s AND name = %s",
            [pid, name]
        ).fetchone()

        if existing:
            raise AppError(409, "DUPLICATE_NAME", "项目中已存在同名文档。")

        # 根据来源类型处理
        template_doc_id = None
        original_file_id = None
        format_template = {}
        markdown_content = ""
        outline = []

        if body.sourceType == "template":
            if not body.templateDocumentId:
                raise AppError(422, "VALIDATION_FAILED", "从模板创建时需要提供模板文档ID。")

            template_doc_id = _uuid(body.templateDocumentId, "templateDocumentId")

            # 获取模板文档信息
            td_row = conn.execute(
                """
                SELECT td.word_file_id, td.format_template, f.storage_key
                FROM template_documents td
                JOIN files f ON f.id = td.word_file_id
                WHERE td.id = %s
                """,
                [template_doc_id]
            ).fetchone()

            if not td_row:
                raise AppError(404, "NOT_FOUND", "模板文档不存在。")

            format_template = td_row[1] if td_row[1] else {}
            storage_key = td_row[2]
            structure = format_template.get("importStructure")
            if not structure or not structure.get("reviewed"):
                raise AppError(409, "TEMPLATE_REVIEW_REQUIRED", "请先在模板管理中核对并确认模板结构，再创建文档。")
            try:
                cleaned = clean_template(resolve_object(get_settings().file_data_dir, storage_key), structure)
                parsed = parse_docx(BytesIO(cleaned), outline_headings=True)
            except ValueError as exc:
                raise AppError(422, "TEMPLATE_STRUCTURE_INVALID", str(exc)) from None
            except Exception as exc:
                raise AppError(422, "WORD_PARSE_FAILED", "Word 文档解析失败，请检查上传的 .docx 文件。") from exc
            markdown_content = parsed.markdown
            outline = parsed.outline
            stored = store_bytes(name=f"{name}.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                 purpose="attachment", content=cleaned, project_id=pid, connection=conn)
            original_file_id = stored.id
            format_template = {**parsed.format_template, "images": parsed.images,
                               "importStructure": structure, "templateSourceFileId": str(td_row[0]), "wordOutlineHeadings": True}

        elif body.sourceType == "upload":
            if not body.wordFileId:
                raise AppError(422, "VALIDATION_FAILED", "上传文档时需要提供文件ID。")

            original_file_id = _uuid(body.wordFileId, "wordFileId")

            # 检查文件存在
            file_row = conn.execute(
                "SELECT id, storage_key FROM files WHERE id = %s AND project_id = %s AND status = 'stored'",
                [original_file_id, pid]
            ).fetchone()

            if not file_row:
                raise AppError(404, "NOT_FOUND", "文件不存在。")

            try:
                parsed = parse_docx(str(resolve_object(get_settings().file_data_dir, file_row[1])))
            except Exception as exc:
                raise AppError(422, "WORD_PARSE_FAILED", "Word 文档解析失败，请检查上传的 .docx 文件。") from exc
            markdown_content = parsed.markdown
            outline = parsed.outline
            format_template = {**parsed.format_template, "images": parsed.images}

        # 创建文档
        doc_id = uuid4()
        conn.execute(
            """
            INSERT INTO documents
            (id, project_id, name, source_type, template_document_id, original_docx_file_id,
             format_template, markdown_content, outline, status, owner_id, revision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, 0)
            """,
            [
                doc_id, pid, name, body.sourceType, template_doc_id, original_file_id,
                json.dumps(format_template), markdown_content, json.dumps(outline),
                session.user_id
            ]
        )
        record_current_version(conn, doc_id, expected_revision=0, kind="created")
        conn.commit()

        # 返回创建的文档
        row = _document_row(conn, doc_id, session.user_id, admin)

        return {
            "data": {
                "id": str(row[0]),
                "projectId": str(row[1]),
                "name": row[2],
                "sourceType": row[3],
                "templateDocumentId": str(row[4]) if row[4] else None,
                "originalDocxFileId": str(row[5]) if row[5] else None,
                "formatTemplate": row[6],
                "markdownContent": row[7],
                "outline": row[8],
                "status": row[9],
                "ownerId": str(row[10]),
                "ownerName": row[11],
                "revision": row[12],
                "updatedAt": format_timestamp(row[13]),
                "projectName": row[14],
                "templateDocumentName": row[15],
            },
            "requestId": request.state.request_id,
        }


@router.get("/documents/{document_id}")
def get_document(request: Request, document_id: str) -> dict[str, object]:
    """获取文档详情（包含 Markdown 内容）"""
    session = document_principal(request)
    admin = _admin(session.roles)
    did = _uuid(document_id, "documentId")

    with connect() as conn:
        row = _document_row(conn, did, session.user_id, admin)

        return {
            "data": {
                "id": str(row[0]),
                "projectId": str(row[1]),
                "name": row[2],
                "sourceType": row[3],
                "templateDocumentId": str(row[4]) if row[4] else None,
                "originalDocxFileId": str(row[5]) if row[5] else None,
                "formatTemplate": row[6],
                "markdownContent": row[7],
                "outline": row[8],
                "status": row[9],
                "ownerId": str(row[10]),
                "ownerName": row[11],
                "revision": row[12],
                "updatedAt": format_timestamp(row[13]),
                "projectName": row[14],
                "templateDocumentName": row[15],
                "permissions": permissions_of(row.project_role, row.admin),
            },
            "requestId": request.state.request_id,
        }


@router.patch("/documents/{document_id}/content")
def update_document_content(
    request: Request,
    document_id: str,
    body: DocumentContentPatchBody
) -> dict[str, object]:
    """更新文档内容（Markdown）"""
    session = document_principal(request)
    admin = _admin(session.roles)
    did = _uuid(document_id, "documentId")

    if body.retractMemoryRevision is not None and (body.retractMemoryRevision != body.expectedRevision or body.memory is not None):
        raise AppError(422, "VALIDATION_FAILED", "只能撤回当前版本的编写记忆，且不能同时新增要求。")

    with connect() as conn:
        # 检查文档和版本
        row = _document_row(conn, did, session.user_id, admin, "edit")

        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "版本冲突，请刷新后重试。")

        if body.memory is not None and _writing_skill(row) is not None:
            raise AppError(422, "VALIDATION_FAILED", "绑定编写规范后，请通过章节、选区或全文采纳接口保存 AI 草稿。")

        _check_word(conn, row, body.markdownContent)
        _save_markdown(conn, did, body.markdownContent, row[12],
                       kind="undo" if body.retractMemoryRevision is not None else "ai_selection" if body.memory else "edit")
        if body.memory:
            record_memory(conn, did, body.memory.instruction, scope=body.memory.scope,
                          target=body.memory.target, revision=row[12] + 1)
        if body.retractMemoryRevision is not None:
            retract_memory(conn, did, body.retractMemoryRevision)
        conn.commit()

        # 返回更新后的文档
        row = _document_row(conn, did, session.user_id, admin)

        return _content_result(request, row)


@router.post("/documents/{document_id}/headings")
def add_heading(request: Request, document_id: str, body: HeadingAddBody) -> dict[str, object]:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    title = body.title.strip()
    if not title or len(title) > 120 or "\n" in title or "\r" in title:
        raise AppError(422, "VALIDATION_FAILED", "标题应为 1–120 字，且不能换行。")
    if body.placement not in {"child", "sibling"}:
        raise AppError(422, "VALIDATION_FAILED", "标题位置不正确。")
    with connect() as conn:
        row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "文档已更新，请刷新后重试。")
        markdown = row[7]
        if body.afterIndex is None:
            level, position, insert_index = 1, len(markdown), len(_headings(markdown))
        else:
            headings, selected = _heading(markdown, body.afterIndex)
            level = int(selected["level"]) + (1 if body.placement == "child" else 0)
            if level > 6:
                raise AppError(422, "VALIDATION_FAILED", "最多支持六级标题。")
            following = next((item for item in headings[body.afterIndex + 1:] if int(item["level"]) <= int(selected["level"])), None)
            position = int(following["start"]) if following else len(markdown)
            insert_index = next((index for index, item in enumerate(headings) if int(item["start"]) >= position), len(headings))
        updated = _insert_at(markdown, position, f"{'#' * level} {title}")
        _check_word(conn, row, updated)
        _save_markdown(conn, did, updated, row[12], kind="heading")
        conn.commit()
        result = _content_result(request, _document_row(conn, did, session.user_id, _admin(session.roles)))
        result["data"]["headingIndex"] = insert_index
        return result


def _section_body(markdown: str, index: int) -> tuple[dict[str, object], str, int]:
    headings, heading = _heading(markdown, index)
    next_start = int(headings[index + 1]["start"]) if index + 1 < len(headings) else len(markdown)
    return heading, markdown[int(heading["bodyStart"]):next_start].strip(), next_start


def _section_update(row, index: int, content: str, mode: str, instruction: str = "") -> str:
    """Use the same format rules and content range for preflight and adoption."""
    heading, existing, position = _section_body(row[7], index)
    rules = section_rules(heading=str(heading["text"]), document_name=row[2],
                          template_name=row[15] or "", instruction=instruction, existing=existing, writing_skill=_writing_skill(row))
    content = validate_draft(content, rules, heading=str(heading["text"]),
                             preserve_images=True, existing=existing)
    if mode == "append":
        if "![" in content:
            raise DraftFormatError("本节草稿包含原有图片，请使用替换，避免重复插图。")
        if rules["tables"] and "|" in existing:
            raise DraftFormatError("本节已有模板表格，请使用替换，避免重复添加表格。")
        updated = _insert_at(row[7], position, content)
        validate_constraints(_section_body(updated, index)[1], rules)
        return updated
    return row[7][:int(heading["bodyStart"])].rstrip() + "\n\n" + content + "\n\n" + row[7][position:]


def _section_adoption_modes(connection, row, index: int, content: str, instruction: str = "") -> list[str]:
    """Check both adoption modes; a protected original paragraph can still allow append."""
    modes, failures = [], []
    for mode in ("replace", "append"):
        try:
            updated = _section_update(row, index, content, mode, instruction)
            _check_word(connection, row, updated)
        except (AppError, DraftFormatError) as exc:
            if isinstance(exc, AppError) and exc.code != "AI_FORMAT_INVALID":
                raise
            failures.append(exc)
        else:
            modes.append(mode)
    if not modes:
        raise failures[0]
    return modes


def _validate_edits(row, edits: list[DocumentEdit], instruction: str = "") -> list[DocumentEdit]:
    seen = set()
    headings = _headings(row[7])
    for edit in edits:
        if edit.headingIndex in seen:
            raise DraftFormatError("同一章节不能重复修改，请重新生成。")
        seen.add(edit.headingIndex)
        if edit.headingIndex is None:
            heading = {"text": "正文"}
            existing = row[7][:int(headings[0]["start"])] if headings else row[7]
        else:
            if edit.headingIndex >= len(headings):
                raise DraftFormatError("全文草稿包含不存在的章节，请重新生成。")
            heading, existing, _ = _section_body(row[7], edit.headingIndex)
        rules = section_rules(heading=str(heading["text"]), document_name=row[2], template_name=row[15] or "", instruction=instruction, existing=existing, writing_skill=_writing_skill(row))
        edit.content = validate_draft(edit.content, rules, heading=str(heading["text"]), preserve_images=True, existing=existing)
    return edits


def _parse_edits(content: str, row, *, complete: bool, read_sections: set[int] | None = None,
                 planned_sections: set[int] | None = None, instruction: str = "") -> list[DocumentEdit]:
    if len(content) > 200000:
        raise DraftFormatError("全文修改草稿过长，请减少本次修改范围。")
    try:
        if complete:
            value = json.loads(content)
            if not isinstance(value, dict) or set(value) != {"edits"} or not isinstance(value["edits"], list) or len(value["edits"]) > 200:
                raise ValueError()
            values = value["edits"]
        else:
            match = re.match(r'^\s*\{\s*"edits"\s*:\s*\[', content)
            if not match:
                return []
            remainder, values = content[match.end():].lstrip(), []
            decoder = json.JSONDecoder()
            while remainder and not remainder.startswith("]"):
                try:
                    value, end = decoder.raw_decode(remainder)
                except json.JSONDecodeError:
                    break
                values.append(value)
                if len(values) > 200:
                    raise ValueError()
                remainder = remainder[end:].lstrip()
                if not remainder.startswith(","):
                    break
                remainder = remainder[1:].lstrip()
        edits = _validate_edits(row, [DocumentEdit.model_validate(value) for value in values], instruction)
        if complete and read_sections is not None:
            headings = _headings(row[7])
            required = {index for index in range(len(headings)) if _section_body(row[7], index)[1]}
            if (row[7][:int(headings[0]["start"])] if headings else row[7]).strip():
                required.add(-1)
            if not required.issubset(read_sections):
                raise DraftFormatError("AI 尚未完整读完文档，请重新生成全文草稿。")
        if complete and planned_sections is not None:
            changed = {-1 if edit.headingIndex is None else edit.headingIndex for edit in edits}
            if not changed.issubset(planned_sections):
                raise DraftFormatError("AI 草稿超出了本次计划的修改章节，请重新生成。")
        return edits
    except (ValueError, ValidationError) as exc:
        if isinstance(exc, DraftFormatError):
            raise
        raise DraftFormatError("AI 全文草稿格式不正确，请重新生成。") from None


def _apply_edits(markdown: str, edits: list[DocumentEdit]) -> str:
    headings = _headings(markdown)
    ranges = []
    for edit in edits:
        if edit.headingIndex is None:
            start, end = 0, int(headings[0]["start"]) if headings else len(markdown)
        else:
            heading, _, end = _section_body(markdown, edit.headingIndex)
            start = int(heading["bodyStart"])
        ranges.append((start, end, edit.content))
    for start, end, content in sorted(ranges, reverse=True):
        markdown = markdown[:start].rstrip() + ("\n\n" if start else "") + content + ("\n\n" if end < len(markdown) else "") + markdown[end:]
    return markdown


@router.get("/documents/{document_id}/memory")
def get_writing_memory(request: Request, document_id: str, offset: int = 0, limit: int = 5) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    if offset < 0 or not 1 <= limit <= 10:
        raise AppError(422, "VALIDATION_FAILED", "记忆查询范围不正确。")
    with connect() as conn:
        _document_row(conn, did, session.user_id, _admin(session.roles))
    items = [{"target": item["target"], "instruction": item["instruction"]}
             for item in read_memory_history(did, offset=offset, limit=limit)]
    has_more = bool(read_memory_history(did, offset=offset + limit, limit=1))
    return {"data": {"summary": load_memory(did), "items": items, "offset": offset,
                     "limit": limit, "hasMore": has_more}, "requestId": request.state.request_id}


@router.delete("/documents/{document_id}/memory")
def delete_writing_memory(request: Request, document_id: str) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        clear_memory(conn, did)
        conn.commit()
    return {"data": {"summary": ""}, "requestId": request.state.request_id}


@router.post("/documents/{document_id}/sections/draft", response_model=None)
def draft_section(request: Request, document_id: str, body: SectionDraftBody, stream: bool = False) -> dict[str, object] | StreamingResponse:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
    if row[12] != body.expectedRevision:
        raise revision_conflict(None, did, "文档已更新，请刷新后重试。")
    whole_document = body.scope == "document"
    selected_only = body.scope == "selection" or body.selectedText is not None or body.selection is not None
    if selected_only:
        if body.scope != "selection" or body.selection is None or body.selectedText != body.selection.text:
            raise AppError(422, "SELECTION_REQUIRED", "请重新选择正文并生成，选区必须包含具体段落和位置。")
        # Reject protected Word locations before spending a model call.
        with connect() as connection:
            _check_word(connection, row, _replace_selection(row[7], body.selection, body.selection.text + "（编写位置检查）"))
    if whole_document and (body.headingIndex is not None or body.selectedText is not None):
        raise AppError(422, "VALIDATION_FAILED", "全文编辑不能同时指定章节或选中文字。")
    if not whole_document and body.headingIndex is None and not (body.selectedText or "").strip():
        raise AppError(422, "VALIDATION_FAILED", "请选择标题或正文文字。")
    if whole_document:
        heading, existing = {"text": "全文", "level": 0}, ""
    elif body.headingIndex is not None:
        heading, existing, _ = _section_body(row[7], body.headingIndex)
    else:
        heading, existing = {"text": "选中内容", "level": 0}, ""
    if body.selectedText is not None:
        existing = body.selectedText
    if len(existing) > 20000:
        raise AppError(422, "VALIDATION_FAILED", "本节正文超过 20000 字，请选中需要修改的部分。")
    settings = get_settings()
    require_ai("writing", settings)
    base_url, model = settings.ai_base_url.strip().rstrip("/"), settings.ai_model.strip()
    read_sections: set[int] = set()
    planned_sections: set[int] = set()
    inputs = dict(
        project_id=row[1], heading=str(heading["text"]), level=int(heading["level"]),
        existing=existing, instruction=body.instruction, base_url=base_url, model=model,
        api_key=settings.ai_api_key, selected_only=selected_only,
        document_name=row[2], template_name=row[15] or "",
        document_id=did, full_markdown=row[7], heading_index=body.headingIndex,
        whole_document=whole_document, context_tokens=settings.ai_context_tokens,
        read_sections=read_sections, planned_sections=planned_sections, source_revision=row[12],
        writing_skill=_writing_skill(row),
        template_structure=(row[6] or {}).get("importStructure"),
    )
    rules = section_rules(heading=inputs["heading"], document_name=row[2], template_name=row[15] or "",
                          instruction=body.instruction, existing=existing, selected_only=inputs["selected_only"], writing_skill=_writing_skill(row))
    inputs["rules"] = rules
    if stream:
        import markdown

        def event(kind: str, content: str = "", message: str = "", allowed_modes: list[str] | None = None, stage: str | None = None) -> str:
            edits = _parse_edits(content, row, complete=kind == "done", instruction=body.instruction) if whole_document and content else []
            headings = _headings(row[7])
            html = (("".join("<h2>" + escape(str(headings[edit.headingIndex]["text"]) if edit.headingIndex is not None else "正文") + "</h2>" + markdown.markdown(edit.content, extensions=["tables"]) for edit in edits) or ("<p>正文没有修改。</p>" if kind == "done" else "")) if whole_document
                    else "<p>" + escape(content).replace("\n", "<br>") + "</p>" if body.selectedText is not None
                    else markdown.markdown(content, extensions=["tables", "fenced_code"]))
            result = {"type": kind, "content": content, "html": html,
                      "revision": row[12], "message": message,
                      "edits": [edit.model_dump() for edit in edits]}
            if allowed_modes is not None:
                result["allowedModes"] = allowed_modes
            if stage is not None:
                result["stage"] = stage
            return json.dumps(result, ensure_ascii=False) + "\n"

        async def generate():
            content, last_sent = "", 0.0
            allowed_modes = None
            upstream = stream_section_text(**inputs, include_progress=True)
            try:
                yield event("start")
                async for update in upstream:
                    if isinstance(update, dict):
                        yield event("progress", message=update["message"], stage=update["stage"])
                        continue
                    content = update
                    if not whole_document:
                        validate_draft(content, rules, selected_only=inputs["selected_only"], complete=False,
                                       preserve_images=not inputs["selected_only"], existing=existing)
                    if monotonic() - last_sent >= 0.05:
                        yield event("draft", content)
                        last_sent = monotonic()
                yield event("progress", message="正在检查正文规范与 Word 排版", stage="validation")
                if whole_document:
                    edits = _parse_edits(content, row, complete=True, read_sections=read_sections,
                                         planned_sections=planned_sections, instruction=body.instruction)
                    with connect() as connection:
                        _check_word(connection, row, _apply_edits(row[7], edits))
                else:
                    content = validate_draft(content, rules, selected_only=inputs["selected_only"], heading=inputs["heading"],
                                             preserve_images=not inputs["selected_only"], existing=existing)
                    if not inputs["selected_only"]:
                        with connect() as connection:
                            allowed_modes = _section_adoption_modes(connection, row, body.headingIndex, content, body.instruction)
                    else:
                        with connect() as connection:
                            _check_word(connection, row, _replace_selection(row[7], body.selection, content))
                yield event("done", content, allowed_modes=allowed_modes)
            except (DraftFormatError, AppError) as exc:
                yield event("error", message=exc.message if isinstance(exc, AppError) else str(exc))
            except Exception:
                yield event("error", message="AI 编写中断，请重试。")
            finally:
                await upstream.aclose()

        return StreamingResponse(generate(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    try:
        content = draft_section_text(**inputs)
    except DraftFormatError as exc:
        raise AppError(502, "AI_FORMAT_INVALID", str(exc)) from None
    except Exception as exc:
        raise AppError(502, "AI_FAILED", "AI 编写失败，请检查模型服务配置后重试。") from exc
    try:
        if whole_document:
            edits = _parse_edits(content, row, complete=True, read_sections=read_sections,
                                 planned_sections=planned_sections, instruction=body.instruction)
            with connect() as connection:
                _check_word(connection, row, _apply_edits(row[7], edits))
            return {"data": {"edits": [edit.model_dump() for edit in edits], "revision": row[12]}, "requestId": request.state.request_id}
        content = validate_draft(content, rules, selected_only=inputs["selected_only"], heading=inputs["heading"],
                                 preserve_images=not inputs["selected_only"], existing=existing)
        allowed_modes = None
        if not inputs["selected_only"]:
            with connect() as connection:
                allowed_modes = _section_adoption_modes(connection, row, body.headingIndex, content, body.instruction)
        else:
            with connect() as connection:
                _check_word(connection, row, _replace_selection(row[7], body.selection, content))
    except DraftFormatError as exc:
        raise AppError(502, "AI_FORMAT_INVALID", str(exc)) from None
    data = {"content": content, "headingIndex": body.headingIndex, "revision": row[12]}
    if allowed_modes is not None:
        data["allowedModes"] = allowed_modes
    return {"data": data, "requestId": request.state.request_id}


def _replace_selection(markdown: str, selection: DocumentSelectionBody, content: str) -> str:
    from app.document_selection import replace_selection, SelectionError
    try:
        return replace_selection(markdown, selection.model_dump(), content)
    except SelectionError as exc:
        raise AppError(422, "SELECTION_INVALID", str(exc)) from None


@router.post("/documents/{document_id}/selection")
def insert_selection(request: Request, document_id: str, body: SelectionInsertBody) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "文档已更新，请重新选择正文并生成。")
        rules = section_rules(heading="选中内容", document_name=row[2], template_name=row[15] or "",
                              instruction=body.instruction, existing=body.selection.text, selected_only=True, writing_skill=_writing_skill(row))
        try:
            content = validate_draft(body.content, rules, selected_only=True, existing=body.selection.text)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        updated = _replace_selection(row[7], body.selection, content)
        _check_word(conn, row, updated)
        _save_markdown(conn, did, updated, row[12], kind="selection")
        record_memory(conn, did, body.instruction, scope="selection", target="选中内容", revision=row[12] + 1)
        conn.commit()
        return _content_result(request, _document_row(conn, did, session.user_id, _admin(session.roles)))


@router.post("/documents/{document_id}/sections/insert")
def insert_section(request: Request, document_id: str, body: SectionInsertBody) -> dict[str, object]:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    content = body.content.strip()
    with connect() as conn:
        row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "文档已更新，请刷新后重试。")
        heading, _, _ = _section_body(row[7], body.headingIndex)
        try:
            updated = _section_update(row, body.headingIndex, content, body.mode, body.instruction)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        _check_word(conn, row, updated)
        _save_markdown(conn, did, updated, row[12], kind="ai_section")
        record_memory(conn, did, body.instruction, scope="section", target=str(heading["text"])[:120], revision=row[12] + 1)
        conn.commit()
        return _content_result(request, _document_row(conn, did, session.user_id, _admin(session.roles)))


@router.post("/documents/{document_id}/edits")
def insert_document_edits(request: Request, document_id: str, body: DocumentInsertBody) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        row = _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "文档已更新，请重新生成全文草稿。")
        try:
            edits = _validate_edits(row, body.edits, body.instruction)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        updated = _apply_edits(row[7], edits)
        _check_word(conn, row, updated)
        _save_markdown(conn, did, updated, row[12], kind="ai_document")
        record_memory(conn, did, body.instruction, scope="document", target=row[2][:120], revision=row[12] + 1)
        conn.commit()
        return _content_result(request, _document_row(conn, did, session.user_id, _admin(session.roles)))


@router.post("/documents/{document_id}/export")
def export_document(
    request: Request,
    document_id: str,
    body: DocumentExportBody
) -> dict[str, object]:
    """导出为 Word 文档"""
    session = document_principal(request)
    admin = _admin(session.roles)
    did = _uuid(document_id, "documentId")

    with connect() as conn:
        row = _document_row(conn, did, session.user_id, admin, "export")

        if row[12] != body.expectedRevision:
            raise revision_conflict(conn, did, "版本冲突，请刷新后重试。")

        markdown_content = row[7]
        document_name = row[2]

        try:
            from tempfile import TemporaryDirectory
            from pathlib import Path
            from app.docx_export import edit_original_docx, diagram_images
            from app.files import store_bytes

            filename = f"{document_name}.docx"
            with TemporaryDirectory() as directory:
                output_path = Path(directory) / "export.docx"
                file_row = conn.execute(
                    "SELECT storage_key FROM files WHERE id = %s AND status = 'stored'",
                    [row[5]],
                ).fetchone()
                if file_row is None:
                    raise AppError(404, "NOT_FOUND", "原始 Word 文件不存在。")
                source = resolve_object(get_settings().file_data_dir, file_row[0])
                edit_original_docx(markdown_content, source, output_path, image_sources=diagram_images(row[6].get("images", [])), outline_headings=bool(row[6].get("wordOutlineHeadings")))
                if _has_word_contents(output_path):
                    try:
                        with _word_slot(), _local_word_document(output_path, read_only=False) as opened:
                            _refresh_word_contents(opened)
                            opened.Save()
                    except AppError:
                        raise
                    except Exception as exc:
                        raise AppError(503, "WORD_CONTENTS_FAILED", "Word 目录更新失败，请检查本机 Microsoft Word。") from exc
                exported = store_bytes(
                    name=filename,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    purpose="attachment",
                    content=output_path.read_bytes(),
                    project_id=row[1],
                )

            return {
                "data": {"fileId": str(exported.id)},
                "requestId": request.state.request_id,
            }
        except ValueError as exc:
            raise AppError(422, "EXPORT_UNSUPPORTED", str(exc)) from None
        except AppError:
            raise
        except Exception:
            raise AppError(500, "EXPORT_FAILED", "导出失败。") from None


@router.get("/documents/{document_id}/preview")
def preview_document(request: Request, document_id: str) -> dict[str, object]:
    """预览文档（返回 HTML）"""
    session = document_principal(request)
    admin = _admin(session.roles)
    did = _uuid(document_id, "documentId")

    with connect() as conn:
        row = _document_row(conn, did, session.user_id, admin)

        from app.document_selection import decorate_selection_html
        html = decorate_selection_html(row[7], _editing_html(conn, row))

        return {
            "data": {"html": html},
            "requestId": request.state.request_id,
        }


def _editing_html(connection, row) -> str:
    from app.docx_preview import render_document_html
    file = connection.execute("SELECT storage_key FROM files WHERE id = %s AND status = 'stored'", [row[5]]).fetchone()
    if file is None:
        raise AppError(404, "NOT_FOUND", "原始 Word 文件不存在。")
    try:
        return render_document_html(resolve_object(get_settings().file_data_dir, file[0]), row[7], row[6].get("images", []), outline_headings=bool(row[6].get("wordOutlineHeadings")))
    except ValueError as exc:
        raise AppError(422, "PREVIEW_UNSUPPORTED", str(exc)) from None


@router.get("/documents/{document_id}/original-preview")
@router.get("/documents/{document_id}/word-preview")
def preview_original_word(request: Request, document_id: str, scope: Literal["original", "edited"] = "original",
                          expectedRevision: int | None = None) -> FileResponse:
    """Render original or current exported OOXML through the local Word installation."""
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        document = _document_row(conn, did, session.user_id, _admin(session.roles))
        if scope == "edited" and expectedRevision != document[12]:
            raise revision_conflict(conn, did, "文档已更新，请刷新后重新预览。")
        file_row = conn.execute(
            "SELECT storage_key FROM files WHERE id = %s AND status = 'stored'",
            [document[5]],
        ).fetchone()
    if file_row is None:
        raise AppError(404, "NOT_FOUND", "原始 Word 文件不存在。")

    source = resolve_object(get_settings().file_data_dir, file_row[0])
    if not source.is_file():
        raise AppError(404, "NOT_FOUND", "原始 Word 文件不存在。")
    preview_dir = source.parent.parent / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    # Invalidate edited PDFs made before the native format inheritance fix.
    # shortcut: cache by revision; add retention cleanup if preview storage grows large.
    target = preview_dir / (f"{did}.{document[12]}.format4.pdf" if scope == "edited" else f"{file_row[0]}.pdf")
    if not target.is_file():
        if not _word_available():
            raise AppError(503, "WORD_UNAVAILABLE", "版式预览需要在本机安装 Microsoft Word。")
        _render_preview(target, preview_dir, did, scope, document, source)
    return FileResponse(
        target,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


def _render_preview(target: Path, preview_dir: Path, did: UUID, scope: str, document, source: Path) -> None:
    lock = _target_lock(str(target))
    if not lock.acquire(timeout=WORD_SLOT_TIMEOUT_SECONDS):
        raise AppError(503, "WORD_BUSY", "Word 正在处理其他文档，请稍后重试。", retry_after=5)
    try:
        if not target.is_file():
            temporary = preview_dir / f"{did}.{uuid4()}.pdf"
            edited_word = temporary.with_suffix(".docx")
            try:
                if scope == "edited":
                    from app.docx_export import edit_original_docx, diagram_images
                    edit_original_docx(document[7], source, edited_word, image_sources=diagram_images(document[6].get("images", [])), outline_headings=bool(document[6].get("wordOutlineHeadings")))
                with _word_slot(), _local_word_document(edited_word if scope == "edited" else source) as opened:
                    if scope == "edited":
                        _refresh_word_contents(opened)
                    opened.ExportAsFixedFormat(str(temporary), 17)
                os.replace(temporary, target)
            except AppError:
                temporary.unlink(missing_ok=True)
                raise
            except ValueError as exc:
                temporary.unlink(missing_ok=True)
                raise AppError(422, "EXPORT_UNSUPPORTED", str(exc)) from None
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                raise AppError(503, "WORD_PREVIEW_FAILED", "版式预览生成失败，请检查本机 Word 是否可用。") from exc
            finally:
                edited_word.unlink(missing_ok=True)
    finally:
        lock.release()

