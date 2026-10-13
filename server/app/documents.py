"""Document instances with Markdown editing."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import asyncio
from dataclasses import replace as _replace_row
import json
import logging
import os
import re
import sys
import weakref
import traceback
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
from openai import APITimeoutError, APIConnectionError, APIStatusError
from httpx import TransportError

from app.db import connect
from app.document_agent import draft_section_text, stream_section_text
from app.document_rules import DraftFormatError, section_rules, validate_draft, validate_constraints, table_writing_notes
from app.document_memory import clear_memory, load_memory, record_memory, read_memory_history, retract_memory, section_memory_targets
from app.document_versions import record_current_version, router as versions_router
from app.docx_parser import parse_docx
from app.docx_export import _html_blocks
from app.ai_capability import require_ai
from app.errors import AppError
from app.config import get_settings
from app.files import resolve_object, store_bytes
from app.template_import import clean_template
from app.document_selection import selection_heading_index, SelectionError
from app.document_commit import _version_markdown, commit_whole, open_counts, replay, request_hash, router as conflicts_router
from app.document_objects import ensure_document_objects, record_commit, revision_conflict, router as objects_router
from app.document_assignments import store_rejected
from app.document_access import (DocumentRow, Need, check_project, document_principal, ensure_project_writable, load_document, member_role,
                                 permissions_of)
from app.models import format_timestamp
from app.writing_skills import load_skill_snapshot

router = APIRouter()
logger = logging.getLogger(__name__)
router.include_router(versions_router)
router.include_router(objects_router)
router.include_router(conflicts_router)
from app.document_patch import router as patch_router  # noqa: E402 - 避免循环导入（document_patch 运行时才取 documents 内函数）
router.include_router(patch_router)
# S3（01 §5.5）：Word COM 仍串行，但只包住 COM 调用；同一预览目标按文件键加锁只生成一次；
# 缓存命中与 edit_original_docx 都在锁外。进程内锁只对单进程部署有效。
_COM_SLOT = BoundedSemaphore(1)
WORD_SLOT_TIMEOUT_SECONDS = 120.0
# 审查 S8：弱引用字典，没有线程持有/等待时自动清掉，不随预览目标数增长
_target_locks: "weakref.WeakValueDictionary[str, Lock]" = weakref.WeakValueDictionary()
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
        raise AppError(503, "WORD_UNAVAILABLE", "服务器未安装 Microsoft Word，含目录的文档暂时不能导出 Word。")
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



MAX_CONTENT_CHARS = 4_000_000

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
    # 增量审查建议 1：正文与显式基准都限制大小（约 4M 字符，远大于 1500 段的需求规格说明书）
    markdownContent: str = Field(max_length=MAX_CONTENT_CHARS)
    memory: WritingMemoryBody | None = None
    retractMemoryRevision: int | None = Field(default=None, ge=0)
    # 阶段2（01 §4.3）：opt-in 三方合并；clientSaveKey 用于幂等重放
    allowMerge: bool = False
    clientSaveKey: str | None = Field(default=None, min_length=8, max_length=80)
    # 增量审查 M1：显式基准正文（仅 allowMerge）。编辑区停在旧正文、又接着改了已保存过的段落时，客户端用
    # “与上次保存对应的编辑区文字”作基准，已保存过的内容就不会被当成对方的修改；expectedRevision 仍是客户端已知的最新修订。
    baseMarkdown: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS)
    # A07 §4.1：与 markdownContent 拆出的顶层块一一对应的对象 ID（新块 null）；编写人员在 enforced 文档上必填
    blockIds: list[str | None] | None = Field(default=None, max_length=20000)


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


class MergeOptions(BaseModel):
    """S6（01 §4.3）：派生写入接口的 opt-in 三方合并；不带时保持严格检查。"""
    allowMerge: bool = False
    clientSaveKey: str | None = Field(default=None, min_length=8, max_length=80)


class HeadingAddBody(MergeOptions):
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


class SelectionInsertBody(MergeOptions):
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
    selectionBlockIds: list[str] | None = Field(default=None, max_length=5000)
    # Spec 30 §4.7: block-ID whitelist of the section; operations outside it are rejected.
    blockIds: list[str] | None = Field(default=None, max_length=500)


class GuidanceQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    scope: Literal["section", "selection", "document", "objects"]
    headingIndex: int | None = Field(default=None, ge=0)
    selection: DocumentSelectionBody | None = None
    objectIds: list[str] = Field(default_factory=list, max_length=200)


class GuidanceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=120)
    text: str | None = Field(max_length=20000)


class GuidancePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    items: list[GuidanceEdit] = Field(min_length=1, max_length=4000)


class SectionInsertBody(MergeOptions):
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


class DocumentInsertBody(MergeOptions):
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
                   kind: str = "edit", restored_from_revision: int | None = None, **commit) -> UUID:
    # 合并说明：正文的分工检查由 A07 在 record_commit / _sync 内统一执行（document_assignments.enforce）。
    if kind == "restore":
        from app.document_assignments import require_scope_write
        from app.document_objects import current_actor
        if current_actor() is not None:
            require_scope_write(connection, document_id, current_actor())
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
                         ai_assisted=kind in _AI_KINDS, **commit)


def merge_base(connection, row: DocumentRow, expected: int, allow_merge: bool, message: str) -> DocumentRow:
    """S6：严格接口版本不符 → 409 REVISION_CONFLICT；allowMerge → 正文换成 versions[expected] 的基线快照。"""
    if row.revision == expected:
        return row
    if not allow_merge:
        raise revision_conflict(connection, row.id, message)
    if expected > row.revision:
        raise AppError(422, "VALIDATION_FAILED", "保存所依据的版本不存在。")
    base = _version_markdown(connection, row.id, expected)
    if base is None:
        raise AppError(409, "BASE_REVISION_UNAVAILABLE", "找不到保存所依据的版本，请刷新后重试。", current_revision=row.revision)
    return _replace_row(row, markdown_content=base, revision=expected)


def commit_derived(connection, session, did: UUID, base: DocumentRow, updated: str, *, kind: str, client_save_key: str | None,
                   images: list[dict] | None = None, template_update=None, after_write=None, extra: dict | None = None) -> dict:
    """S6：派生写入（AI 采纳、标题、图表、测试结果）在基线上算好新全文后，与当前正文三方合并提交。"""
    admin = _admin(session.roles)

    def check(markdown: str) -> None:
        fresh = _document_row(connection, did, session.user_id, admin, "edit")
        merged_images = None if images is None else list((fresh[6] or {}).get("images", [])) + images
        _check_word(connection, fresh, markdown, images=merged_images)

    return commit_whole(connection, document_id=did, actor=session.user_id, base_revision=base.revision, mine_md=updated,
                        kind=kind, source=kind, client_save_key=client_save_key, check_word=check, extra=extra,
                        after_write=after_write, template_update=template_update)


WHITESPACE_NOTES = ("whitespaceDropped", "whitespaceDeleted", "theirsWhitespaceOverridden", "frozenByConflict", "snapshotLimited")


def _merged_result(request: Request, connection, session, did: UUID, data: dict) -> dict:
    result = _content_result(request, _document_row(connection, did, session.user_id, _admin(session.roles)))
    result["data"].update(merged=data["merged"], changeId=data["changeId"], changedBy=data["changedBy"])
    if data.get("replayed"):
        result["data"]["replayed"] = True
    for key in WHITESPACE_NOTES:  # 终验 S-1 及终审建议：只改空白的修改没被写入/被覆盖时的提示计数
        if data.get(key):
            result["data"][key] = data[key]
    return result


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


# 测试 L3：原稿某段无法安全写回是“结构类”问题，不是 AI 输出格式问题；手工保存也会遇到。
WORD_BLOCK_PROTECTED = "WORD_BLOCK_PROTECTED"
WORD_WRITE_CODES = frozenset({"AI_FORMAT_INVALID", WORD_BLOCK_PROTECTED})


def word_format_error(content: str, exc: ValueError) -> AppError:
    """422 WORD_BLOCK_PROTECTED with the failing block's location (01 §4.10)."""
    from app.docx_export import _html_blocks
    from app.document_location import compute_locations, html_block_items, location_text
    index = getattr(exc, "block_index", None)
    if index is None:
        return AppError(422, WORD_BLOCK_PROTECTED, str(exc))
    try:
        blocks = _html_blocks(content)
        location = compute_locations(html_block_items(blocks))[index]
        block_text = " ".join("".join(blocks[index].itertext()).split())
    except (IndexError, ValueError):
        return AppError(422, WORD_BLOCK_PROTECTED, str(exc))
    # details.reason 只写原因，界面说明条用它避免位置写两遍（界面验收 S8）；message 保留位置给其他入口。
    details = {"location": location, "reason": str(exc), "blockText": block_text[:2000]}
    removed = getattr(exc, "removed_text", None)
    if removed:
        details["deletedText"] = removed[:2000]  # 界面复验 N1：被拒的删除，原段落文字
    return AppError(422, WORD_BLOCK_PROTECTED, f"{location_text(location)}：{exc}", details=details)


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
            # 合并说明：分工限制由下方 A07 检查（只在设置确有变化时拒绝，details.reason=assignment）
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
                # 27 复验 N-3 / 03 Q-5（待用户确认）：按分工编辑的文档上，整篇的编写规范与写作要求只有项目负责人和管理员能改
                from app.document_assignments import actor_scope, restricted
                if restricted(*actor_scope(conn, did, session.user_id)):
                    raise AppError(403, "FORBIDDEN", "按分工编辑的文档，编写规范和整篇写作要求只有项目负责人可以修改。",
                                   details={"reason": "assignment"})
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
    if need == "edit":
        ensure_project_writable(connection, project_id)
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
        archived = conn.execute("SELECT archived_at FROM projects WHERE id = %s", [pid]).fetchone()["archived_at"] is not None
        permissions = permissions_of(role, admin, archived)

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
        # 01 §4.4.2：未处理冲突数（一次 GROUP BY）；memberOpenConflicts 只给负责人/管理员
        counts = open_counts(conn, [row[0] for row in rows], session.user_id)
        manager = admin or role == "owner"
        for item in items:
            mine, total = counts.get(item["id"], (0, 0))
            item["mineOpenConflicts"] = mine
            item["memberOpenConflicts"] = total if manager else None

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
        template_version_id = None
        original_file_id = None
        format_template = {}
        markdown_content = ""
        outline = []
        target_paths = {}

        if body.sourceType == "template":
            if not body.templateDocumentId:
                raise AppError(422, "VALIDATION_FAILED", "从模板创建时需要提供模板文档ID。")

            template_doc_id = _uuid(body.templateDocumentId, "templateDocumentId")

            # A06：读取当前已发布版本（不可变，事务外读取也一致），不读草稿（17 §3.5“新建文档”）
            from app.template_versions import ensure, word_file_id
            from app.template_word import clean_version

            exists = conn.execute("SELECT id FROM template_documents WHERE id = %s", [template_doc_id]).fetchone()
            if not exists:
                raise AppError(404, "NOT_FOUND", "模板文档不存在。")
            ensure(template_doc_id, session.user_id)
            version = conn.execute(
                """
                SELECT v.id, v.version, v.source_file_id, v.edited_file_id, v.structure_json, f.storage_key
                FROM template_documents td
                JOIN template_document_versions v ON v.id = td.current_version_id
                JOIN files f ON f.id = coalesce(v.edited_file_id, v.source_file_id)
                WHERE td.id = %s
                """,
                [template_doc_id]
            ).fetchone()
            if not version:
                raise AppError(409, "TEMPLATE_REVIEW_REQUIRED", "请先在模板管理中核对并确认模板结构，再创建文档。")
            structure = json.loads(version["structure_json"])
            template_version_id = version["id"]
            word_id = word_file_id({"edited_file_id": version["edited_file_id"], "source_file_id": version["source_file_id"]})
            word_path = resolve_object(get_settings().file_data_dir, version["storage_key"])
            try:
                if version["version"] == 1 and version["edited_file_id"] is None:
                    cleaned = clean_template(word_path, structure, target_paths=target_paths)
                else:
                    cleaned = clean_version(word_path, structure, target_paths=target_paths)
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
                               "importStructure": structure, "templateSourceFileId": str(word_id), "wordOutlineHeadings": True}

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
             format_template, markdown_content, outline, status, owner_id, revision, template_version_id, assignment_mode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, 0, %s, %s)
            """,
            [
                doc_id, pid, name, body.sourceType, template_doc_id, original_file_id,
                json.dumps(format_template), markdown_content, json.dumps(outline),
                session.user_id, template_version_id, NEW_DOCUMENT_MODE
            ]
        )
        if target_paths:
            from app.document_objects import live_objects
            from app.template_guidance import bind_guidance_targets
            ensure_document_objects(conn, doc_id)
            try:
                targets = bind_guidance_targets(cleaned, structure, markdown_content, live_objects(conn, doc_id), target_paths=target_paths)
            except ValueError as error:
                raise AppError(422, "GUIDANCE_TARGET_UNMATCHED", str(error)) from None
            format_template["guidanceTargets"] = targets
            conn.execute("UPDATE documents SET format_template=%s WHERE id=%s", (json.dumps(format_template), doc_id))
        record_current_version(conn, doc_id, expected_revision=0, kind="created")
        # A07 U-16 / §10.4：编写人员新建的文档整篇默认归创建者（@document）；负责人、管理员新建的不分配
        if not admin and member_role(conn, pid, session.user_id) == "editor":
            conn.execute(
                """INSERT INTO document_object_assignments (document_id, object_id, assignee_id, source, assigned_by, assigned_at)
                   VALUES (%s, '@document', %s, 'creator', %s, now())""", [doc_id, session.user_id, session.user_id])
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


def _template_info(conn, document_id, template_document_id, document_name, group_name) -> dict | None:
    """A06 文档信息：模板 · 第 N 版；结构早于版本化的老文档 legacySnapshot=true，不显示版本号（16 §3.4）。"""
    if template_document_id is None:
        return None
    row = conn.execute(
        """SELECT d.template_version_id, v.version, v.published_at,
                  (SELECT max(x.version) FROM template_document_versions x
                    WHERE x.template_document_id = d.template_document_id AND x.status <> 'draft') AS latest
           FROM documents d LEFT JOIN template_document_versions v ON v.id = d.template_version_id WHERE d.id = %s""",
        (document_id,),
    ).fetchone()
    legacy = row["template_version_id"] is None
    return {"name": group_name, "documentName": document_name,
            "versionId": None if legacy else str(row["template_version_id"]),
            "seq": None if legacy else row["version"],
            "publishedAt": None if legacy or row["published_at"] is None else format_timestamp(row["published_at"]),
            "latestSeq": row["latest"], "legacySnapshot": legacy}


@router.get("/documents/{document_id}")
def get_document(request: Request, document_id: str) -> dict[str, object]:
    """获取文档详情（包含 Markdown 内容）"""
    session = document_principal(request)
    admin = _admin(session.roles)
    did = _uuid(document_id, "documentId")

    with connect() as conn:
        row = _document_row(conn, did, session.user_id, admin)
        # 终验 S-3：工作台模板标签显示模板组名（如“GJB 438C-2021”），而不是与标题重复的模板文档名
        group = conn.execute(
            "SELECT t.name FROM template_documents td JOIN templates t ON t.id = td.template_id WHERE td.id = %s",
            (row[4],),
        ).fetchone() if row[4] else None
        template = _template_info(conn, did, row[4], row[15], None if group is None else group[0])

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
                "templateName": None if group is None else group[0],
                "template": template,
                "permissions": _permissions_a07(conn, row, session.user_id),
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

    if body.allowMerge and body.retractMemoryRevision is not None:
        raise AppError(422, "VALIDATION_FAILED", "撤回 AI 修改需要基于当前版本，不能合并保存。")
    if body.baseMarkdown is not None and not body.allowMerge:
        raise AppError(422, "VALIDATION_FAILED", "显式基准只能用于合并保存。")

    with connect() as conn:
        # 检查文档和版本
        row = _document_row(conn, did, session.user_id, admin, "edit")
        block_ids = _checked_block_ids(conn, did, session.user_id, body)
        try:
            return _update_content(request, conn, session, did, row, body, block_ids)
        except AppError as error:
            if error.code == "SECTION_NOT_ASSIGNED":
                store_rejected(conn, did, session.user_id, body.expectedRevision, body.markdownContent, block_ids, error)
            raise


# A07 U-14 / H-9：新建文档默认按分工编辑（已有文档迁移时为 open）
NEW_DOCUMENT_MODE = "enforced"


def _permissions_a07(conn, row, actor: UUID) -> dict:
    """详情 permissions：A05 字段 + A07 assignmentMode / canAssign / editableHeadings / projectOwners（17 §4.6）。"""
    from app.assignments_api import permissions_extra
    return {**permissions_of(row.project_role, row.admin, row.archived), **permissions_extra(conn, row, actor)}


def _checked_block_ids(conn, did: UUID, actor: UUID, body: DocumentContentPatchBody) -> list[str | None] | None:
    """A07 §4.5.2 / §4.5.6：编写人员在 enforced 文档上整篇保存必须带 blockIds，且块数一致。"""
    from app.document_assignments import actor_scope, restricted
    from app.markdown_blocks import split

    limited = restricted(*actor_scope(conn, did, actor))
    if body.blockIds is None:
        if limited:
            raise AppError(422, "BLOCK_IDS_REQUIRED", "页面版本过旧，请刷新后再保存。你的内容已保留在本机。")
        return None
    if len(body.blockIds) != len(split(body.markdownContent)[1]):
        if limited:
            raise AppError(422, "VALIDATION_FAILED", "块标识与正文不一致，请刷新后再保存。",
                           field_errors=[{"field": "blockIds", "message": "块标识的数量与正文的块数不一致。"}])
        return None  # 负责人、管理员：忽略不一致的块标识，走服务端兜底配对
    return list(body.blockIds)


def _update_content(request: Request, conn, session, did: UUID, row, body: DocumentContentPatchBody,
                    block_ids: list[str | None] | None) -> dict[str, object]:
    admin = _admin(session.roles)
    if body.allowMerge:
        return _merge_content(request, conn, session, did, row, body, block_ids)
    rhash = request_hash({"base": body.expectedRevision, "markdown": body.markdownContent, "kind": "strict",
                          "memory": body.memory.model_dump() if body.memory else None,
                          "retract": body.retractMemoryRevision})
    if body.clientSaveKey and replay(conn, did, session.user_id, body.clientSaveKey, rhash) is not None:
        return _content_result(request, _document_row(conn, did, session.user_id, admin))

    if row[12] != body.expectedRevision:
        raise revision_conflict(conn, did, "版本冲突，请刷新后重试。")

    if body.memory is not None and _writing_skill(row) is not None:
        raise AppError(422, "VALIDATION_FAILED", "绑定编写规范后，请通过章节、选区或全文采纳接口保存 AI 草稿。")

    _check_word(conn, row, body.markdownContent)
    _save_markdown(conn, did, body.markdownContent, row[12],
                   kind="undo" if body.retractMemoryRevision is not None else "ai_selection" if body.memory else "edit",
                   client_save_key=body.clientSaveKey, request_hash=rhash if body.clientSaveKey else None,
                   identities=block_ids)
    if body.memory:
        record_memory(conn, did, body.memory.instruction, scope=body.memory.scope,
                      target=body.memory.target, revision=row[12] + 1)
    if body.retractMemoryRevision is not None:
        retract_memory(conn, did, body.retractMemoryRevision)
    conn.commit()

    # 返回更新后的文档
    row = _document_row(conn, did, session.user_id, admin)

    return _content_result(request, row)


def _merge_content(request: Request, conn, session, did: UUID, row, body: DocumentContentPatchBody,
                   block_ids: list[str | None] | None = None) -> dict[str, object]:
    """allowMerge：以 versions[expectedRevision] 为基线块级三方合并（01 §4.3、§5.2、§5.4）。"""
    if body.memory is not None and _writing_skill(row) is not None:
        raise AppError(422, "VALIDATION_FAILED", "绑定编写规范后，请通过章节、选区或全文采纳接口保存 AI 草稿。")
    admin = _admin(session.roles)
    memory = body.memory

    def after_write(connection, revision: int) -> None:
        if memory:
            record_memory(connection, did, memory.instruction, scope=memory.scope, target=memory.target, revision=revision)

    data = commit_whole(
        conn, document_id=did, actor=session.user_id, base_revision=body.expectedRevision, mine_md=body.markdownContent,
        base_markdown=body.baseMarkdown, block_ids=block_ids,
        kind="ai_selection" if memory else "edit", client_save_key=body.clientSaveKey,
        check_word=lambda markdown: _check_word(conn, _document_row(conn, did, session.user_id, admin, "edit"), markdown),
        extra={"memory": memory.model_dump() if memory else None}, after_write=after_write)
    result = _content_result(request, _document_row(conn, did, session.user_id, admin))
    result["data"].update(merged=data["merged"], changeId=data["changeId"], changedBy=data["changedBy"])
    if data.get("replayed"):
        result["data"]["replayed"] = True
    for key in WHITESPACE_NOTES:  # 终验 S-1 及终审建议：只改空白的修改没被写入/被覆盖时的提示计数
        if data.get(key):
            result["data"][key] = data[key]
    return result


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
        row = merge_base(conn, _document_row(conn, did, session.user_id, _admin(session.roles), "edit"),
                         body.expectedRevision, body.allowMerge, "文档已更新，请刷新后重试。")
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
        if body.allowMerge:
            data = commit_derived(conn, session, did, row, updated, kind="heading", client_save_key=body.clientSaveKey)
            result = _merged_result(request, conn, session, did, data)
            # 合并后标题序号按服务器全文重新定位（同名取基线序号附近的那个）。
            texts = [str(item["text"]) for item in _headings(str(result["data"]["markdownContent"]))]
            near = [index for index, text in enumerate(texts) if text == title]
            result["data"]["headingIndex"] = min(near, key=lambda index: abs(index - insert_index)) if near else insert_index
            return result
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
        if any(block.tag == "table" for block in _html_blocks(existing)) and any(block.tag == "table" for block in _html_blocks(content)):
            raise DraftFormatError("本节已有模板表格，请使用替换，避免重复添加表格。")
        updated = _insert_at(row[7], position, content)
        validate_constraints(_section_body(updated, index)[1], rules)
        return updated
    return row[7][:int(heading["bodyStart"])].rstrip() + "\n\n" + content + "\n\n" + row[7][position:]


def _block_result(connection, row, index: int, content: str, scope: dict, rules: dict, heading: str,
                  existing: str, instruction: str = "") -> tuple[str, list[dict], list[str]]:
    """Validate block operations, compose the section for Word checks and render each operation."""
    import markdown as md
    from app.block_ops import apply_operations, parse_operations
    operations = parse_operations(content, scope["heading"], scope["blocks"])
    composed = apply_operations(scope["heading"], scope["blocks"], operations)
    if operations:
        validate_draft(composed, rules, heading=heading, preserve_images=True, existing=existing)
        modes = _section_adoption_modes(connection, row, index, composed, instruction, in_place=True)
    else:
        modes = ["replace"]
    for item in operations:
        item["html"] = md.markdown(item["content"], extensions=["tables"]) if item["content"] else ""
    return composed, operations, modes


def _section_adoption_modes(connection, row, index: int, content: str, instruction: str = "", *, in_place: bool = False) -> list[str]:
    """Check both adoption modes; a protected original paragraph can still allow append.

    An in-place draft is the existing body with fragments patched, so appending it
    would duplicate the section: only replacement is offered.
    """
    modes, failures = [], []
    for mode in (("replace",) if in_place else ("replace", "append")):
        try:
            updated = _section_update(row, index, content, mode, instruction)
            _check_word(connection, row, updated)
        except (AppError, DraftFormatError) as exc:
            if isinstance(exc, AppError) and exc.code not in WORD_WRITE_CODES:
                raise
            failures.append(exc)
        else:
            modes.append(mode)
    if not modes:
        raise failures[0]
    return modes


def _validate_edits(row, edits: list[DocumentEdit], instruction: str = "", *, complete: bool = True) -> list[DocumentEdit]:
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
        edit.content = validate_draft(edit.content, rules, heading=str(heading["text"]), preserve_images=True,
                                     existing=existing, complete=complete)
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
        edits = _validate_edits(row, [DocumentEdit.model_validate(value) for value in values], instruction, complete=complete)
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
    from app.document_assignments import require_scope_write
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        _document_row(conn, did, session.user_id, _admin(session.roles), "edit")
        require_scope_write(conn, did, session.user_id)
        clear_memory(conn, did)
        conn.commit()
    return {"data": {"summary": ""}, "requestId": request.state.request_id}


def _selected_heading(markdown: str, selection: DocumentSelectionBody | None) -> int | None:
    if selection is None:
        raise AppError(422, "SELECTION_REQUIRED", "请先选择正文文字。")
    try:
        return selection_heading_index(markdown, selection.model_dump())
    except SelectionError as error:
        raise AppError(422, "SELECTION_CHANGED", str(error)) from None


@router.post("/documents/{document_id}/writing-guidance/query")
def query_writing_guidance(request: Request, document_id: str, body: GuidanceQueryBody) -> dict:
    from app.document_guidance import object_spans, selection_ids, effective_advice, EDITABLE_KINDS
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as connection:
        row = _document_row(connection, did, session.user_id, _admin(session.roles))
        objects = object_spans(connection, row)
        connection.commit()
    if row[12] != body.expectedRevision:
        raise revision_conflict(None, did)
    headings = _headings(row[7])
    heading_paths, stack = {}, []
    for index, heading in enumerate(headings):
        while stack and stack[-1][0] >= heading["level"]:
            stack.pop()
        stack.append((heading["level"], heading["text"]))
        heading_paths[index] = " / ".join(str(text) for _level, text in stack)
    selected_ids = None
    if body.scope == "objects":
        if not body.objectIds or len(set(body.objectIds)) != len(body.objectIds):
            raise AppError(422, "VALIDATION_FAILED", "请选择不重复的正文段落或表格。")
        selected = [item for item in objects if item["id"] in body.objectIds and item["kind"] in EDITABLE_KINDS]
        if len(selected) != len(body.objectIds):
            raise AppError(422, "GUIDANCE_TARGET_UNMATCHED", "部分对象已删除或不支持设置段落建议。")
        groups = [{"headingIndex": None if item["headingIndex"] < 0 else item["headingIndex"], "heading": "本段建议",
                   "items": [{"id": "object:" + item["id"], "heading": "本段建议", "kind": "guidance", "sourceText": table_writing_notes(item["body"]),
                              "text": (row[6] or {}).get("blockGuidance", {}).get(item["id"], table_writing_notes(item["body"])),
                              "overridden": item["id"] in (row[6] or {}).get("blockGuidance", {}),
                              "targetObjectIds": [item["id"]], "targetText": item["label"]}]} for item in selected]
        return {"data": {"revision": row[12], "groups": groups, "notices": []}, "requestId": request.state.request_id}
    if body.scope == "document":
        indices = [None, *range(len(headings))]
    elif body.scope == "selection":
        indices = [_selected_heading(row[7], body.selection)]
        selected_ids = selection_ids(row[7], body.selection.model_dump(), objects)
    else:
        if body.headingIndex is None or body.headingIndex >= len(headings):
            raise AppError(422, "VALIDATION_FAILED", "请选择有效章节。")
        indices = [body.headingIndex]
    template = row[6] or {}
    groups, notices = [], []
    for index in indices:
        try:
            items = effective_advice(template, row[7], index, objects, selected_ids)
        except ValueError as error:
            raise AppError(422, "GUIDANCE_UNMATCHED", str(error)) from None
        notices.extend(item["text"] for item in items if item["kind"] == "notice")
        items = [item for item in items if item["kind"] != "notice"]
        if index is not None or items or not headings:
            groups.append({"headingIndex": index, "heading": "正文" if index is None else headings[index]["text"],
                           "headingPath": "正文" if index is None else heading_paths[index], "items": items})
    return {"data": {"revision": row[12], "groups": groups, "notices": list(dict.fromkeys(notices))},
            "requestId": request.state.request_id}


@router.patch("/documents/{document_id}/writing-guidance")
def set_writing_guidance(request: Request, document_id: str, body: GuidancePatchBody) -> dict:
    from app.document_guidance import object_spans, EDITABLE_KINDS
    from app.document_assignments import require_scope_write
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as connection:
        with connection.transaction(immediate=True):
            row = _document_row(connection, did, session.user_id, _admin(session.roles), "edit")
            if row[12] != body.expectedRevision:
                raise revision_conflict(connection, did)
            template = row[6] or {}
            structure = template.get("importStructure") or {}
            objects = object_spans(connection, row)
            editable = {item["id"] for item in objects if item["kind"] in EDITABLE_KINDS}
            source = {item["id"]: item["text"] for item in structure.get("blocks", [])
                      if item["kind"] in {"guidance", "placeholder", "example"}}
            if (any(not item.id.startswith("object:") for item in body.items) and not structure.get("reviewed")) or len({item.id for item in body.items}) != len(body.items):
                raise AppError(422, "VALIDATION_FAILED", "编写建议来源未确认或重复。")
            overrides = dict(template.get("writingGuidance", {}))
            block_guidance = dict(template.get("blockGuidance", {}))
            for item in body.items:
                if item.id.startswith("object:"):
                    object_id = item.id.removeprefix("object:")
                    if object_id not in editable:
                        raise AppError(422, "GUIDANCE_TARGET_UNMATCHED", "编写建议目标不存在或不支持。")
                    require_scope_write(connection, did, session.user_id, [object_id])
                    if item.text is None or not item.text.strip():
                        block_guidance.pop(object_id, None)
                    else:
                        block_guidance[object_id] = item.text.strip()
                    continue
                if item.id not in source:
                    raise AppError(422, "VALIDATION_FAILED", "编写建议不属于此文档的模板。")
                require_scope_write(connection, did, session.user_id, template.get("guidanceTargets", {}).get(item.id))
                if item.text is None or item.text.strip() == source[item.id]:
                    overrides.pop(item.id, None)
                else:
                    if not item.text.strip():
                        raise AppError(422, "VALIDATION_FAILED", "编写建议不能为空；可恢复模板原文。")
                    overrides[item.id] = item.text.strip()
            if overrides != template.get("writingGuidance", {}) or block_guidance != template.get("blockGuidance", {}):
                ensure_document_objects(connection, did)
                record_current_version(connection, did, expected_revision=row[12])
                updated = connection.execute(
                    "UPDATE documents SET format_template=%s, revision=revision+1, updated_at=now() WHERE id=%s AND revision=%s",
                    [json.dumps({**template, "writingGuidance": overrides, "blockGuidance": block_guidance}, ensure_ascii=False), did, row[12]],
                )
                if updated.rowcount != 1:
                    raise revision_conflict(connection, did)
                record_current_version(connection, did, expected_revision=row[12]+1, kind="writing_guidance")
                record_commit(connection, did, base_revision=row[12], markdown=None, kind="writing_guidance")
            row = _document_row(connection, did, session.user_id, _admin(session.roles))
            result = _content_result(request, row)
            result["data"]["formatTemplate"] = row[6]
            return result


async def _keep_draft_alive(upstream, interval=15):
    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(upstream))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                # 空行保活，不改写用户看到的编写阶段或尚未完成的草稿。
                yield "\n"
                continue
            ready, pending = pending, None
            try:
                item = ready.result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await upstream.aclose()


@router.post("/documents/{document_id}/sections/draft", response_model=None)
def draft_section(request: Request, document_id: str, body: SectionDraftBody, stream: bool = False) -> dict[str, object] | StreamingResponse:
    from app.document_guidance import object_spans, selection_ids, effective_advice, writing_context
    from app.document_assignments import require_scope_write, require_manage
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
        actual_heading = _selected_heading(row[7], body.selection)
        if body.headingIndex is not None and body.headingIndex != actual_heading:
            raise AppError(422, "GUIDANCE_SCOPE_MISMATCH", "选中文字与当前章节不一致，请重新选择正文。")
        body.headingIndex = actual_heading
        # Reject protected Word locations before spending a model call.
        with connect() as connection:
            probe = _replace_selection(row[7], body.selection, body.selection.text + "（编写位置检查）")
            _check_word(connection, row, probe)
            # 27 复验 M-3：选中文字的 AI 也在调用模型前按所选块的归属预检
            from app.document_assignments import selection_precheck
            selection_precheck(connection, did, session.user_id, probe, body.selectionBlockIds, body.selection.text)
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
    with connect() as connection:  # A07：调模型之前先查分工范围（不扣额度）
        from app.document_assignments import ai_precheck
        # 28 I-4：预检与写作用同一个标题（写作按正则取第 headingIndex 个标题，预检按它的文字核对对象）
        ai_precheck(connection, did, session.user_id, body.headingIndex, whole_document,
                    heading_text=str(heading.get("text", "")) if body.headingIndex is not None else None)
    settings = get_settings()
    require_ai("writing", settings)
    base_url, model = settings.ai_base_url.strip().rstrip("/"), settings.ai_model.strip()
    read_sections: set[int] = set()
    planned_sections: set[int] = set()
    with connect() as connection:
        from app.document_references import writing_references
        references = writing_references(connection, row, session)
        memory_targets = section_memory_targets(connection, did, row[7], row[12])
        objects = object_spans(connection, row)
        target_ids = selection_ids(row[7], body.selection.model_dump(), objects) if selected_only else [
            item["id"] for item in objects if item["kind"] != "heading" and
            (whole_document or item["headingIndex"] == (body.headingIndex if body.headingIndex is not None else -1))]
        word_headings = _word_heading_ids(connection, row, objects) if not selected_only and not whole_document and existing.strip() else set()
        # Word heading paragraphs are locked anchors, never edit targets: no write permission is needed for them.
        target_ids = [item for item in target_ids if item not in word_headings]
        if body.blockIds is not None and not selected_only and not whole_document:
            # Spec 30 §4.2: the writing scope is the blocks of this section the user may change.
            target_ids = [item for item in target_ids if item in set(body.blockIds)]
            if not target_ids:
                raise AppError(409, "AI_SCOPE_VIOLATION", "写作范围内没有你能修改的正文，请刷新后重试。")
        if target_ids:
            require_scope_write(connection, did, session.user_id, target_ids)
        elif whole_document:
            require_manage(connection, did, session.user_id)
        # 空章节：归属已由上面的 A07 ai_precheck 按标题链判断
        try:
            context = writing_context(row[6] or {}, row[7], objects, None if whole_document else [body.headingIndex if body.headingIndex is not None else -1])
            advice = effective_advice(row[6] or {}, row[7], body.headingIndex, objects, target_ids) if selected_only else None
        except ValueError as error:
            raise AppError(422, "GUIDANCE_UNMATCHED", str(error)) from None
        connection.commit()
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
        guidance_overrides=(row[6] or {}).get("writingGuidance", {}),
        sequential_document=whole_document,
        # Existing body is edited in place via exact-fragment replacements; only empty bodies are drafted fresh.
        edit_in_place=whole_document or (not selected_only and bool(existing.strip())),
        memory_targets=memory_targets,
        guidance_context=context,
        references=references,
    )
    block_scope = None
    if not selected_only and not whole_document and existing.strip():
        from app.block_ops import scope_blocks
        scope_heading, scope_list = scope_blocks(objects, body.headingIndex, locked=word_headings)
        if body.blockIds is not None:
            allowed = set(body.blockIds)
            if any(item not in {block["id"] for block in scope_list} | ({scope_heading["id"]} if scope_heading else set()) for item in allowed):
                raise AppError(409, "AI_SCOPE_VIOLATION", "写作范围已变化，请刷新后重试。")
            scope_list = [block for block in scope_list if block["id"] in allowed or block.get("locked")]
        if scope_list:
            block_scope = {"heading": scope_heading, "blocks": scope_list}
            inputs.update(block_scope=block_scope, edit_in_place=False)
    if selected_only:
        inputs["effective_guidance"] = advice
    elif not whole_document:
        module = context.get(body.headingIndex, {})
        inputs["effective_guidance"] = module.get("guidance", [])
        start = int(heading["bodyStart"])
        # Existing section text is stripped; preserve offsets relative to that exact text.
        start += len(row[7][start:]) - len(row[7][start:].lstrip())
        if block_scope is None:
            inputs["writing_modules"] = [{**item, "start": item["start"] - start, "end": item["end"] - start}
                                         for item in module.get("modules", [])]
    rules = section_rules(heading=inputs["heading"], document_name=row[2], template_name=row[15] or "",
                          instruction=body.instruction, existing=existing, selected_only=inputs["selected_only"], writing_skill=_writing_skill(row))
    inputs["rules"] = rules
    if stream:
        import markdown

        def event(kind: str, content: str = "", message: str = "", allowed_modes: list[str] | None = None, stage: str | None = None,
                  operations: list[dict] | None = None) -> str:
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
            if operations is not None:
                result["operations"] = operations
            if stage is not None:
                result["stage"] = stage
            return json.dumps(result, ensure_ascii=False) + "\n"

        async def generate():
            content, last_sent = "", 0.0
            stage = "preparing"
            target = "全文" if whole_document else inputs["heading"]
            allowed_modes = None
            upstream = stream_section_text(**inputs, include_progress=True)
            try:
                yield event("start")
                async for update in upstream:
                    if isinstance(update, dict):
                        stage = update["stage"]
                        if stage == "module" and "：" in update["message"]:
                            target = update["message"]
                        yield event("progress", message=update["message"], stage=update["stage"])
                        continue
                    content = update
                    if block_scope is not None:
                        continue
                    if not whole_document:
                        validate_draft(content, rules, selected_only=inputs["selected_only"], complete=False,
                                       preserve_images=not inputs["selected_only"], existing=existing)
                    if monotonic() - last_sent >= 0.05:
                        yield event("draft", content)
                        last_sent = monotonic()
                yield event("progress", message="正在检查正文规范与 Word 排版", stage="validation")
                stage = "validation"
                if block_scope is not None:
                    def check_blocks():
                        with connect() as connection:
                            return _block_result(connection, row, body.headingIndex, content, block_scope, rules,
                                                 inputs["heading"], existing, body.instruction)
                    composed, operations, modes = await asyncio.to_thread(check_blocks)
                    yield event("done", composed, allowed_modes=modes, operations=operations)
                    return
                if not whole_document:
                    content = validate_draft(content, rules, selected_only=inputs["selected_only"], heading=inputs["heading"],
                                             preserve_images=not inputs["selected_only"], existing=existing)

                def check_word():
                    with connect() as connection:
                        if whole_document:
                            edits = _parse_edits(content, row, complete=True, read_sections=read_sections,
                                                 planned_sections=planned_sections, instruction=body.instruction)
                            _check_word(connection, row, _apply_edits(row[7], edits))
                        elif not inputs["selected_only"]:
                            return _section_adoption_modes(connection, row, body.headingIndex, content, body.instruction, in_place=inputs["edit_in_place"])
                        else:
                            _check_word(connection, row, _replace_selection(row[7], body.selection, content))
                    return None

                allowed_modes = await asyncio.to_thread(check_word)
                yield event("done", content, allowed_modes=allowed_modes)
            except (DraftFormatError, AppError) as exc:
                yield event("error", message=exc.message if isinstance(exc, AppError) else str(exc))
            except APITimeoutError:
                yield event("error", message="模型服务长时间未返回内容，请检查模型服务，或调大 AI_READ_TIMEOUT_SECONDS 后重试。")
            except Exception as exc:
                # 不记录异常正文：模型服务错误可能携带密钥、提示词或用户原文。
                logger.error("document draft failed request=%s document=%s stage=%s error=%s status=%s\n%s",
                             request.state.request_id, did, stage, type(exc).__name__, getattr(exc, "status_code", None),
                             "".join(traceback.format_tb(exc.__traceback__)))
                if isinstance(exc, (APIConnectionError, TransportError)):
                    message = "模型服务连接中断"
                elif isinstance(exc, APIStatusError):
                    message = f"模型服务请求失败（HTTP {exc.status_code}）"
                else:
                    message = "编写服务处理失败"
                yield event("error", message=f"{message}；{target}。原文未修改，请重试。")
            finally:
                await upstream.aclose()

        return StreamingResponse(_keep_draft_alive(generate()), media_type="application/x-ndjson",
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
        if block_scope is not None:
            with connect() as connection:
                composed, operations, modes = _block_result(connection, row, body.headingIndex, content, block_scope, rules,
                                                            inputs["heading"], existing, body.instruction)
            return {"data": {"content": composed, "operations": operations, "headingIndex": body.headingIndex,
                             "revision": row[12], "allowedModes": modes}, "requestId": request.state.request_id}
        content = validate_draft(content, rules, selected_only=inputs["selected_only"], heading=inputs["heading"],
                                 preserve_images=not inputs["selected_only"], existing=existing)
        allowed_modes = None
        if not inputs["selected_only"]:
            with connect() as connection:
                allowed_modes = _section_adoption_modes(connection, row, body.headingIndex, content, body.instruction, in_place=inputs["edit_in_place"])
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
        row = merge_base(conn, _document_row(conn, did, session.user_id, _admin(session.roles), "edit"),
                         body.expectedRevision, body.allowMerge, "文档已更新，请重新选择正文并生成。")
        rules = section_rules(heading="选中内容", document_name=row[2], template_name=row[15] or "",
                              instruction=body.instruction, existing=body.selection.text, selected_only=True, writing_skill=_writing_skill(row))
        try:
            content = validate_draft(body.content, rules, selected_only=True, existing=body.selection.text)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        updated = _replace_selection(row[7], body.selection, content)
        if body.allowMerge:
            data = commit_derived(conn, session, did, row, updated, kind="selection", client_save_key=body.clientSaveKey,
                                  after_write=lambda c, revision: record_memory(c, did, body.instruction, scope="selection",
                                                                                 target="选中内容", revision=revision),
                                  extra={"instruction": body.instruction})
            return _merged_result(request, conn, session, did, data)
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
        row = merge_base(conn, _document_row(conn, did, session.user_id, _admin(session.roles), "edit"),
                         body.expectedRevision, body.allowMerge, "文档已更新，请刷新后重试。")
        heading, _, _ = _section_body(row[7], body.headingIndex)
        try:
            updated = _section_update(row, body.headingIndex, content, body.mode, body.instruction)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        target = section_memory_targets(conn, did, row[7], row[12]).get(body.headingIndex)
        if body.instruction.strip() and target is None:
            raise revision_conflict(conn, did, "目标标题已变化，无法确定编写要求所属章节，请刷新后重新生成。")
        if body.allowMerge:
            data = commit_derived(conn, session, did, row, updated, kind="ai_section", client_save_key=body.clientSaveKey,
                                  after_write=lambda c, revision: record_memory(c, did, body.instruction, scope="section",
                                                                                 target=target or "", revision=revision),
                                  extra={"instruction": body.instruction})
            return _merged_result(request, conn, session, did, data)
        _check_word(conn, row, updated)
        _save_markdown(conn, did, updated, row[12], kind="ai_section")
        record_memory(conn, did, body.instruction, scope="section", target=target or "", revision=row[12] + 1)
        conn.commit()
        return _content_result(request, _document_row(conn, did, session.user_id, _admin(session.roles)))


@router.post("/documents/{document_id}/edits")
def insert_document_edits(request: Request, document_id: str, body: DocumentInsertBody) -> dict:
    session = document_principal(request)
    did = _uuid(document_id, "documentId")
    with connect() as conn:
        row = merge_base(conn, _document_row(conn, did, session.user_id, _admin(session.roles), "edit"),
                         body.expectedRevision, body.allowMerge, "文档已更新，请重新生成全文草稿。")
        try:
            edits = _validate_edits(row, body.edits, body.instruction)
        except DraftFormatError as exc:
            raise AppError(422, "AI_FORMAT_INVALID", str(exc)) from None
        updated = _apply_edits(row[7], edits)
        if body.allowMerge:
            target = row[2][:120]
            data = commit_derived(conn, session, did, row, updated, kind="ai_document", client_save_key=body.clientSaveKey,
                                  after_write=lambda c, revision: record_memory(c, did, body.instruction, scope="document",
                                                                                 target=target, revision=revision),
                                  extra={"instruction": body.instruction})
            return _merged_result(request, conn, session, did, data)
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
        from app.document_objects import decorate_object_ids
        html = decorate_selection_html(row[7], _editing_html(conn, row))
        # S7：能证明一一对应时为顶层块加对象 ID；ensure 可能补建对象行，需提交。
        ensure_document_objects(conn, did)
        if conn.in_transaction:
            conn.commit()
        html, object_model = decorate_object_ids(conn, did, html)

        return {
            "data": {"html": html, "objectModel": object_model, "revision": row[12]},
            "requestId": request.state.request_id,
        }


def _word_heading_ids(connection, row, objects: list[dict]) -> set[str]:
    """Object IDs of Word heading paragraphs kept as body text (spec 30 §4.3: locked like headings)."""
    from lxml import html as html_dom
    from app.document_objects import decorate_object_ids
    if not row[5]:
        return set()
    try:
        html, mapped = decorate_object_ids(connection, row[0], _editing_html(connection, row))
    except AppError:
        logger.warning("word heading detection unavailable document=%s", row[0])
        return set()
    if not mapped:
        return set()
    root = html_dom.fragment_fromstring(html, create_parent="div")
    known = {item["id"] for item in objects}
    return {node.get("data-object-id") for node in root.xpath(".//*[@data-word-heading][@data-object-id]")} & known


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
            raise AppError(503, "WORD_UNAVAILABLE", "服务器未安装 Microsoft Word，暂时不能生成版式预览。")
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

