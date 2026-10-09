"""Persistent, editable writing rules; builtin sources remain identifiable."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.db import DictConnection, connect
from app.errors import AppError
from app.identity import require_csrf, require_session
from app.models import format_timestamp

router = APIRouter()
BaseKind = Literal["general", "srs", "sdd"]
SkillStatus = Literal["active", "disabled"]


class SkillValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(min_length=1, max_length=12000)
    baseKind: BaseKind = "general"
    forbiddenTerms: list[str] = Field(default_factory=list, max_length=30)
    maxChars: int | None = Field(default=None, ge=100, le=20000, strict=True)
    status: SkillStatus = "active"

    @field_validator("name", "description", "instructions", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("forbiddenTerms")
    @classmethod
    def clean_terms(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            term = value.strip()
            if not term or len(term) > 80:
                raise ValueError("禁止词长度应在 1-80 字符之间。")
            if term not in cleaned:
                cleaned.append(term)
        return cleaned


class SkillPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    baseKind: BaseKind | None = None
    forbiddenTerms: list[str] | None = None
    maxChars: int | None = Field(default=None, ge=100, le=20000, strict=True)
    status: SkillStatus | None = None


class SkillSnapshot(SkillValues):
    id: UUID
    revision: int = Field(ge=0)
    builtin: bool
    updatedAt: str


def validate_skill_snapshot(value: Mapping[str, object]) -> dict:
    """Pure validation for a saved complete skill snapshot; performs no IO."""
    try:
        return SkillSnapshot.model_validate(value).model_dump(mode="json")
    except ValidationError:
        raise AppError(422, "VALIDATION_FAILED", "编写技能内容不正确。") from None


def _uuid(value: str | UUID) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise AppError(422, "VALIDATION_FAILED", "技能标识不正确。") from None


def _public(row) -> dict:
    return {
        "id": str(row["id"]), "name": row["name"], "description": row["description"],
        "instructions": row["instructions"], "baseKind": row["base_kind"],
        "forbiddenTerms": json.loads(row["forbidden_terms"]), "maxChars": row["max_chars"],
        "status": row["status"], "revision": row["revision"], "builtin": bool(row["builtin"]),
        "updatedAt": format_timestamp(row["updated_at"]),
    }


def load_skill_snapshot(skill_id: str | UUID, *, require_active: bool = True,
                        connection: DictConnection | None = None) -> dict:
    """Load a trusted server snapshot for document-generation integration."""
    target = _uuid(skill_id)
    with (connect() if connection is None else nullcontext(connection)) as active_connection:
        row = active_connection.execute("SELECT * FROM writing_skills WHERE id = %s", (target,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "没有这个编写技能。")
    if require_active and row["status"] != "active":
        raise AppError(409, "SKILL_DISABLED", "该编写技能已停用，请重新选择。")
    return _public(row)


def _admin(request: Request):
    actor = require_session(request)
    if "system_admin" not in actor.roles:
        raise AppError(403, "FORBIDDEN", "只有系统管理员可以管理编写技能。")
    require_csrf(request, actor)
    return actor


def _params(values: dict) -> tuple:
    return (
        values["name"], values["name"].casefold(), values["description"], values["instructions"],
        values["baseKind"], json.dumps(values["forbiddenTerms"], ensure_ascii=False),
        values["maxChars"], values["status"],
    )


def _timestamp() -> str:
    # SQLite CURRENT_TIMESTAMP has only second precision; retain write order.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


_COMMON = (
    "依据当前文档、已确认项目信息和本次要求编写正式技术正文。\n"
    "缺失或未确认的数据应明确标为待补充或待确认，不得编造单位、人员、版本、指标、接口或测试结论。\n"
    "沿用现有标题层级、段落与表格结构、术语和已确认标识；只修改指定范围。\n"
    "正文应具体、简洁、可核对，不添加自我介绍、编写过程说明或与当前内容无关的描述。\n"
    "表格必须保留真实模板的表头、列顺序与列数，数据行与表头对应；不得将样例内容当成项目事实。\n"
    "保留文档中的图片、引用关系和已有事实；目录、页眉页脚及复杂 Word 域由文档格式处理，勿改写成普通正文。"
)
BUILTINS = (
    {
        "id": "438c0000-0000-4000-8000-000000000001", "name": "通用技术文档",
        "description": "技术文档的正文、术语、事实与表格约束。", "baseKind": "general",
        "instructions": _COMMON,
    },
    {
        "id": "438c0000-0000-4000-8000-000000000002", "name": "GJB438C软件需求规格说明",
        "description": "依据内置 GJB438C SRS 规范填写需求与追踪内容。", "baseKind": "srs",
        "instructions": _COMMON + "\n"
        "按照真实 SRS 模板组织范围、引用文档、需求、合格性规定、需求可追踪性与注释。\n"
        "需求应说明条件、行为、边界及可验证的验收依据；性能等量化约束只使用已确认数据。\n"
        "保留已有需求标识，维护需求与合格性规定及正向、逆向追踪关系；未知对应关系明确待确认。\n"
        "只填写当前章节正文与表格数据；标题、模板锚点、表头和编号结构保持一致。",
    },
    {
        "id": "438c0000-0000-4000-8000-000000000003", "name": "GJB438C软件设计说明",
        "description": "依据内置 GJB438C SDD 规范填写体系结构、接口与单元设计。", "baseKind": "sdd",
        "instructions": _COMMON + "\n"
        "按照真实 SDD 模板组织范围、引用文档、设计决策、体系结构、详细设计、需求可追踪性与注释。\n"
        "依据已提供设计或源码说明部件职责、数据流、控制关系和接口；输入输出、异常与边界均需有依据。\n"
        "对接口及软件单元使用一致的已有标识，说明设计与需求之间的正向、逆向追踪关系。\n"
        "只填写当前范围正文与表格数据；保留模板的章节原型、真实表头与既有样式，不猜测缺失设计。",
    },
)


def seed_writing_skills() -> None:
    """Stable IDs seed once, including after builtin rename/disable/edit."""
    with connect() as connection:
        with connection.transaction(immediate=True):
            for item in BUILTINS:
                values = SkillValues.model_validate({key: value for key, value in item.items() if key != "id"}).model_dump()
                connection.execute(
                    """INSERT OR IGNORE INTO writing_skills
                       (id, name, name_key, description, instructions, base_kind, forbidden_terms,
                        max_chars, status, builtin, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)""",
                    (_uuid(item["id"]), *_params(values), _timestamp()),
                )


@router.get("/writing-skills")
def list_skills(request: Request, page: int = 1, pageSize: int = 20,
                name: str = "", status: str | None = None) -> dict:
    require_session(request)
    if page < 1 or not 1 <= pageSize <= 50 or status not in {None, "active", "disabled"}:
        raise AppError(422, "VALIDATION_FAILED", "查询条件不正确。")
    clauses, params = [], []
    if name.strip():
        cleaned = name.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("name_key LIKE %s ESCAPE '\\'")
        params.append(f"%{cleaned}%")
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = " AND ".join(clauses) or "1"
    with connect() as connection:
        total = connection.execute(f"SELECT COUNT(*) AS total FROM writing_skills WHERE {where}", params).fetchone()["total"]
        rows = connection.execute(
            f"SELECT * FROM writing_skills WHERE {where} ORDER BY updated_at DESC, name_key, id LIMIT %s OFFSET %s",
            [*params, pageSize, (page - 1) * pageSize],
        ).fetchall()
    return {"data": {"items": [_public(row) for row in rows], "total": total, "page": page, "pageSize": pageSize},
            "requestId": request.state.request_id}


@router.get("/writing-skills/{skill_id}")
def get_skill(skill_id: str, request: Request) -> dict:
    require_session(request)
    return {"data": load_skill_snapshot(skill_id, require_active=False), "requestId": request.state.request_id}


@router.post("/writing-skills", status_code=201)
def create_skill(request: Request, body: SkillValues) -> dict:
    actor = _admin(request)
    values = body.model_dump()
    key = request.headers.get("Idempotency-Key", "").strip()
    if len(key) > 128:
        raise AppError(422, "VALIDATION_FAILED", "本次请求的稳定键过长。")
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    with connect() as connection:
        with connection.transaction(immediate=True):
            if key:
                existing = connection.execute("SELECT request_hash, skill_id FROM writing_skill_requests WHERE user_id=%s AND idempotency_key=%s", (actor.user_id, key)).fetchone()
                if existing:
                    if existing["request_hash"] != digest:
                        raise AppError(409, "IDEMPOTENCY_CONFLICT", "同一个请求键不能改成其他内容。")
                    return {"data": load_skill_snapshot(existing["skill_id"], require_active=False, connection=connection), "requestId": request.state.request_id}
            if connection.execute("SELECT id FROM writing_skills WHERE name_key=%s", (values["name"].casefold(),)).fetchone():
                raise AppError(409, "DUPLICATE_NAME", "已存在同名编写技能。")
            skill_id = uuid4()
            connection.execute(
                """INSERT INTO writing_skills
                   (id, name, name_key, description, instructions, base_kind, forbidden_terms, max_chars, status, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (skill_id, *_params(values), _timestamp()),
            )
            if key:
                connection.execute("INSERT INTO writing_skill_requests(user_id,idempotency_key,request_hash,skill_id) VALUES (%s,%s,%s,%s)", (actor.user_id, key, digest, skill_id))
            created = load_skill_snapshot(skill_id, require_active=False, connection=connection)
    return {"data": created, "requestId": request.state.request_id}


@router.patch("/writing-skills/{skill_id}")
def update_skill(skill_id: str, request: Request, body: SkillPatch) -> dict:
    _admin(request)
    target = _uuid(skill_id)
    updates = body.model_dump(exclude_unset=True, exclude={"expectedRevision"})
    if not updates:
        raise AppError(422, "VALIDATION_FAILED", "请填写需要修改的技能内容。")
    with connect() as connection:
        with connection.transaction(immediate=True):
            current = load_skill_snapshot(target, require_active=False, connection=connection)
            if current["revision"] != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "技能刚被修改，请刷新后重新保存。", current_revision=current["revision"])
            if current["builtin"] and "baseKind" in updates and updates["baseKind"] != current["baseKind"]:
                raise AppError(422, "IMMUTABLE_FIELD", "内置技能的规范类型不能修改。")
            merged = {field: current[field] for field in SkillValues.model_fields}
            merged.update(updates)
            try:
                values = SkillValues.model_validate(merged).model_dump()
            except ValidationError:
                raise AppError(422, "VALIDATION_FAILED", "编写技能内容不正确。") from None
            if connection.execute("SELECT id FROM writing_skills WHERE name_key=%s AND id<>%s", (values["name"].casefold(), target)).fetchone():
                raise AppError(409, "DUPLICATE_NAME", "已存在同名编写技能。")
            connection.execute(
                """UPDATE writing_skills SET name=%s,name_key=%s,description=%s,instructions=%s,
                   base_kind=%s,forbidden_terms=%s,max_chars=%s,status=%s,
                   revision=revision+1,updated_at=%s WHERE id=%s AND revision=%s""",
                (*_params(values), _timestamp(), target, body.expectedRevision),
            )
            updated = load_skill_snapshot(target, require_active=False, connection=connection)
    return {"data": updated, "requestId": request.state.request_id}
