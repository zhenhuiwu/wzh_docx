"""Stored files. HTTP writes require a server session and permission."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path, PurePath
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.config import get_settings
from app.db import connect
from app.errors import AppError
from app.models import FileRecord

router = APIRouter()

_UUID = re.compile(
    "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MEDIA_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "text/plain",
}
_PURPOSES = {"attachment", "evidence", "source", "template"}


def authorize(request: Request, permission: str):
    """A browser-stored role is not a credential."""

    from app.identity import require_permission

    return require_permission(request, permission)


def guard_knowledge_file(request: Request, file_id: str) -> None:
    """Download follows the file's own project or knowledge scope."""

    from app.identity import require_session
    from app.knowledge import _admin, _require_base
    from app.permissions import has_permission
    from app.projects import project_role

    actor = require_session(request)
    admin = _admin(actor.roles)
    with connect() as connection:
        file_row = connection.execute(
            "select project_id, base_id, purpose from files where id = %s",
            (file_id,),
        ).fetchone()
        if file_row is None:
            return
        if admin:
            return
        base_ids: list[object] = []
        if file_row["base_id"] is not None:
            base_ids.append(file_row["base_id"])
        linked = connection.execute(
            """
            select s.base_id
            from source_versions v
            join sources s on s.id = v.source_id
            where v.file_id = %s
            """,
            (file_id,),
        ).fetchall()
        for item in linked:
            if item["base_id"] not in base_ids:
                base_ids.append(item["base_id"])
        if base_ids:
            for base_id in base_ids:
                _require_base(connection, base_id, actor.user_id, False)
            return
        if file_row["project_id"] is not None:
            if project_role(connection, file_row["project_id"], actor.user_id) is None:
                raise AppError(404, "NOT_FOUND", "没有这个文件。")
            return
        if file_row["purpose"] == "template" and has_permission(actor.roles, "templates.read"):
            return
        raise AppError(404, "NOT_FOUND", "没有这个文件。")


def parse_file_id(file_id: str) -> str:
    if not _UUID.fullmatch(file_id):
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "文件标识不正确。",
            [{"field": "fileId", "message": "需要有效的文件标识。"}],
        )
    return file_id.lower()


def prepare_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    (resolved / "tmp").mkdir(parents=True, exist_ok=True)
    (resolved / "objects").mkdir(parents=True, exist_ok=True)
    return resolved


def resolve_object(root: Path, storage_key: str) -> Path:
    """Map a storage key to one file inside objects/. Reject anything else."""

    if (
        not _UUID.fullmatch(storage_key)
        or "/" in storage_key
        or "\\" in storage_key
    ):
        raise AppError(422, "VALIDATION_FAILED", "文件位置不正确。")
    base = prepare_root(root)
    objects = (base / "objects").resolve()
    candidate = (objects / storage_key).resolve()
    if candidate.parent != objects or not candidate.is_relative_to(base):
        raise AppError(422, "VALIDATION_FAILED", "文件位置不正确。")
    return candidate


def clean_name(name: str) -> str:
    cleaned = name.strip()
    if (
        not cleaned
        or len(cleaned) > 255
        or "/" in cleaned
        or "\\" in cleaned
        or ".." in cleaned
    ):
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "文件名不正确。",
            [{"field": "file", "message": "文件名不能包含路径。"}],
        )
    return cleaned


def record_from_row(row: dict[str, object]) -> FileRecord:
    project_id = row["project_id"]
    base_id = row["base_id"]
    return FileRecord(
        id=row["id"],  # type: ignore[arg-type]
        name=str(row["name"]),
        media_type=str(row["media_type"]),
        size=int(row["size_bytes"]),  # type: ignore[arg-type]
        sha256=str(row["sha256"]),
        status=str(row["status"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        purpose=str(row["purpose"]),
        project_id=project_id if isinstance(project_id, UUID) else None,
        base_id=base_id if isinstance(base_id, UUID) else None,
        storage_key=str(row["storage_key"]),
    )


def store_bytes(
    *,
    name: str,
    media_type: str,
    purpose: str,
    content: bytes,
    project_id: UUID | None = None,
    base_id: UUID | None = None,
    connection=None,
) -> FileRecord:
    """Write a temp file, check it, then commit the formal object and the row."""

    settings = get_settings()
    safe_name = clean_name(name)
    if media_type not in _MEDIA_TYPES:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "这个文件类型还不能保存。",
            [{"field": "file", "message": "文件类型不在允许范围内。"}],
        )
    if purpose not in _PURPOSES:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "文件用途不正确。",
            [{"field": "purpose", "message": "用途不在允许范围内。"}],
        )
    if purpose == "source" and base_id is None:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "资料文件需要所属知识库。",
            [{"field": "baseId", "message": "不能为空。"}],
        )
    if purpose == "evidence" and project_id is None:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "证据文件需要所属项目。",
            [{"field": "projectId", "message": "不能为空。"}],
        )
    if len(content) > settings.file_max_bytes:
        raise AppError(429, "LIMIT_EXCEEDED", "文件超过允许的大小。")

    file_id = uuid4()
    storage_key = str(file_id)
    digest = hashlib.sha256(content).hexdigest()
    root = prepare_root(settings.file_data_dir)
    temporary = root / "tmp" / f"{storage_key}.part"
    final_path = resolve_object(root, storage_key)
    temporary.write_bytes(content)
    written = temporary.read_bytes()
    if written != content or hashlib.sha256(written).hexdigest() != digest:
        temporary.unlink(missing_ok=True)
        raise AppError(500, "INTERNAL_ERROR", "文件没有完整写入。")
    os.replace(temporary, final_path)
    row = None
    try:
        own_connection = connection is None
        with (connect() if own_connection else nullcontext(connection)) as connection:
            with (connection.transaction(immediate=True) if own_connection else nullcontext()):
                row = connection.execute(
                    """
                    insert into files (
                      id, name, media_type, size_bytes, sha256, status,
                      purpose, project_id, base_id, storage_key
                    )
                    values (
                      %s, %s, %s, %s, %s, 'stored',
                      %s, %s, %s, %s
                    )
                    returning id, name, media_type, size_bytes, sha256, status,
                              created_at, purpose, project_id, base_id,
                              storage_key
                    """,
                    (
                        file_id,
                        safe_name,
                        media_type,
                        len(content),
                        digest,
                        purpose,
                        project_id,
                        base_id,
                        storage_key,
                    ),
                ).fetchone()
    except AppError:
        final_path.unlink(missing_ok=True)
        raise
    except Exception:
        final_path.unlink(missing_ok=True)
        raise AppError(500, "INTERNAL_ERROR", "文件记录没有保存。") from None
    if row is None:
        final_path.unlink(missing_ok=True)
        raise AppError(500, "INTERNAL_ERROR", "文件记录没有保存。")
    return record_from_row(row)


def load_record(file_id: str) -> FileRecord | None:
    with connect() as connection:
        row = connection.execute(
            """
            select id, name, media_type, size_bytes, sha256, status,
                   created_at, purpose, project_id, base_id, storage_key
            from files
            where id = %s
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return record_from_row(row)


def read_object_bytes(root: Path, storage_key: str) -> bytes:
    path = resolve_object(root, storage_key)
    if not path.is_file():
        raise AppError(404, "NOT_FOUND", "文件不存在。")
    return path.read_bytes()


def _ascii_filename(name: str) -> str:
    """A quoted-string safe fallback: printable ASCII without quotes or backslashes.

    Non-ASCII names fall back to "download" plus a safe extension; browsers
    use the RFC 5987 filename* parameter for the real name.
    """
    def safe(value: str) -> str:
        return "".join(char if " " <= char <= "~" and char not in '"\\' else "_" for char in value)

    if not name.isascii():
        suffix = PurePath(name).suffix
        return "download" + (suffix if suffix.isascii() and safe(suffix) == suffix and len(suffix) <= 16 else "")
    return safe(name).strip(" .")[:150] or "download"


def content_disposition(disposition: str, name: str) -> str:
    """Never echo control characters or quotes from a stored file name into a header."""
    encoded = quote(name, safe="")
    return f"{disposition}; filename=\"{_ascii_filename(name)}\"; filename*=UTF-8''{encoded}"


def _file_response(record: FileRecord, inline: bool) -> Response:
    payload = read_object_bytes(get_settings().file_data_dir, record.storage_key)
    disposition = "inline" if inline else "attachment"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": content_disposition(disposition, record.name),
    }
    return Response(content=payload, media_type=record.media_type, headers=headers)


def _load_visible(file_id: str) -> FileRecord:
    record = load_record(file_id)
    if record is None or record.status != "stored":
        raise AppError(404, "NOT_FOUND", "文件不存在。")
    return record


@router.post("/files", status_code=201)
async def upload_file(request: Request) -> dict[str, object]:
    principal = authorize(request, "files.upload")
    form = await request.form()
    upload = form.get("file")
    purpose = form.get("purpose")
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if not idempotency_key or len(idempotency_key) > 128:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "缺少本次请求的稳定键。",
            [{"field": "Idempotency-Key", "message": "需要填写。"}],
        )
    if not hasattr(upload, "read") or not isinstance(purpose, str):
        raise AppError(422, "VALIDATION_FAILED", "上传内容不正确。")
    content = await upload.read()
    project_id = _optional_uuid(form.get("projectId"), "projectId")
    base_id = _optional_uuid(form.get("baseId"), "baseId")
    from app.identity import require_permission
    from app.knowledge import _require_writer
    from app.projects import require_project_editor

    if purpose == "template":
        require_permission(request, "templates.manage")
    if purpose == "source":
        require_permission(request, "knowledge.manage")
    if project_id is None and base_id is None and purpose != "template" and "system_admin" not in principal.roles:
        raise AppError(422, "VALIDATION_FAILED", "请选择文件所属项目或知识库。")
    metadata = json.dumps(
        [purpose, str(project_id or ""), str(base_id or ""),
         str(getattr(upload, "filename", "") or "file"),
         str(getattr(upload, "content_type", "") or "application/octet-stream")],
        ensure_ascii=False, separators=(",", ":"),
    )
    request_hash = hashlib.sha256(
        metadata.encode("utf-8") + b"\0" + content
    ).hexdigest()
    existing = record = None
    try:
        with connect() as connection:
            with connection.transaction(immediate=True):
                if project_id is not None:
                    project = connection.execute("SELECT archived_at FROM projects WHERE id = %s", (project_id,)).fetchone()
                    if project is None:
                        raise AppError(404, "NOT_FOUND", "没有这个项目。")
                    require_project_editor(connection, principal, project_id)
                    if project["archived_at"] is not None:
                        raise AppError(409, "INVALID_TRANSITION", "项目已归档，无法上传文件。")
                if base_id is not None:
                    _require_writer(connection, base_id, principal.user_id, "system_admin" in principal.roles)
                existing = _idempotent_file(principal.user_id, idempotency_key, request_hash)
                record = existing or store_bytes(
                    name=str(getattr(upload, "filename", "") or "file"),
                    media_type=str(getattr(upload, "content_type", "") or "application/octet-stream"),
                    purpose=purpose,
                    content=content,
                    project_id=project_id,
                    base_id=base_id,
                    connection=connection,
                )
                if existing is None:
                    connection.execute(
                        """INSERT INTO idempotency_keys (user_id, idempotency_key, request_hash, file_id)
                           VALUES (%s, %s, %s, %s)""",
                        (principal.user_id, idempotency_key, request_hash, record.id),
                    )
    except Exception:
        if existing is None and record is not None:
            resolve_object(get_settings().file_data_dir, record.storage_key).unlink(missing_ok=True)
        raise
    return {"data": record.public_dict(), "requestId": request.state.request_id}


def _optional_uuid(value: object, field: str) -> UUID | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise AppError(422, "VALIDATION_FAILED", "标识不正确。")
    try:
        return UUID(value)
    except ValueError:
        raise AppError(
            422,
            "VALIDATION_FAILED",
            "标识不正确。",
            [{"field": field, "message": "需要有效标识。"}],
        ) from None


def _idempotent_file(
    user_id: UUID,
    key: str,
    request_hash: str,
) -> FileRecord | None:
    with connect() as connection:
        row = connection.execute(
            """
            select request_hash, file_id
            from idempotency_keys
            where user_id = %s and idempotency_key = %s
            """,
            (user_id, key),
        ).fetchone()
    if row is None:
        return None
    if row["request_hash"] != request_hash or row["file_id"] is None:
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一个请求键不能改成另一份内容。")
    record = load_record(str(row["file_id"]))
    if record is None:
        raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一个请求键不能改成另一份内容。")
    return record


def authorize_read(request: Request, file_id: str) -> None:
    """项目文件对项目成员可读（S1：只有项目角色的账号也能下载原稿和导出稿）；其余仍要求 files.read。"""

    from app.identity import require_session
    from app.permissions import has_permission
    from app.projects import project_role

    actor = require_session(request)
    if has_permission(actor.roles, "files.read"):
        return
    with connect() as connection:
        row = connection.execute("select project_id, base_id from files where id = %s", (file_id,)).fetchone()
        if (row is not None and row["project_id"] is not None and row["base_id"] is None
                and project_role(connection, row["project_id"], actor.user_id) is not None):
            return
    raise AppError(403, "FORBIDDEN", "没有执行这个操作的权限。")


@router.get("/files/{file_id}/content")
def download_file(file_id: str, request: Request) -> Response:
    parsed = parse_file_id(file_id)
    authorize_read(request, parsed)
    guard_knowledge_file(request, parsed)
    return _file_response(_load_visible(parsed), inline=False)


@router.get("/files/{file_id}/preview")
def preview_file(file_id: str, request: Request) -> Response:
    parsed = parse_file_id(file_id)
    authorize_read(request, parsed)
    guard_knowledge_file(request, parsed)
    return _file_response(_load_visible(parsed), inline=True)
