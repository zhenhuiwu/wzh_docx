"""内置文档模板与文档依赖链（30 §3.1、§3.6）。

本轮不新增迁移：依赖关系由内置目录（代码）给出，项目内按文档名称或来源模板名称匹配。
项目级可编辑的上游关系（30 Q-1）需要新表，待 A07 合入、迁移编号确定后再做。
"""

from __future__ import annotations

from io import BytesIO
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request

from app.db import connect
from app.document_access import document_principal, is_admin
from app.errors import AppError
from app.identity import require_permission
from app.markdown_blocks import heading_text, split

router = APIRouter()

BUILTIN_GROUP = "内置软件文档模板"
BUILTIN_CATEGORY = "软件研制"

STAGES = [
    {"key": "initiate", "name": "立项"},
    {"key": "requirement", "name": "需求"},
    {"key": "design", "name": "设计与计划"},
    {"key": "delivery", "name": "交付"},
]

# 公共框架章节：多份文档共用、单独分配负责人（30 §3.4）。key 只在数据里使用。
FRAMEWORKS = [
    {"key": "runtime", "name": "软件运行环境"},
    {"key": "glossary", "name": "术语和缩略语"},
    {"key": "references", "name": "引用文档"},
]

# 章节写成 (级别, 标题, 公共框架 key 或 None)
CATALOG = [
    {"key": "task-book", "name": "任务计划书", "stage": "initiate", "upstream": [],
     "description": "说明任务来源、目标、范围和进度要求。",
     "outline": [(1, "1 范围", None), (2, "1.1 标识", None), (2, "1.2 系统概述", None), (1, "2 引用文档", "references"),
                 (1, "3 任务目标", None), (1, "4 任务范围", None), (1, "5 进度要求", None), (1, "6 术语和缩略语", "glossary")]},
    {"key": "srs", "name": "软件需求规格说明", "stage": "requirement", "upstream": ["task-book"],
     "description": "逐项描述功能、性能、接口与质量需求。",
     "outline": [(1, "1 范围", None), (2, "1.1 标识", None), (1, "2 引用文档", "references"), (1, "3 需求", None),
                 (2, "3.1 运行环境", "runtime"), (2, "3.2 功能需求", None), (2, "3.3 性能需求", None), (2, "3.4 接口需求", None),
                 (2, "3.5 质量需求", None), (1, "4 术语和缩略语", "glossary")]},
    {"key": "dev-plan", "name": "软件开发计划", "stage": "design", "upstream": ["srs"],
     "description": "规定开发活动、任务分解、人员分工与进度。",
     "outline": [(1, "1 范围", None), (1, "2 引用文档", "references"), (1, "3 开发环境", "runtime"), (1, "4 任务分解", None),
                 (1, "5 公共框架分工", None), (1, "6 进度安排", None), (1, "7 术语和缩略语", "glossary")]},
    {"key": "design", "name": "软件概要设计说明", "stage": "design", "upstream": ["srs"],
     "description": "描述软件体系结构、部件与接口设计。",
     "outline": [(1, "1 范围", None), (1, "2 引用文档", "references"), (1, "3 运行环境", "runtime"), (1, "4 体系结构设计", None),
                 (1, "5 接口设计", None), (1, "6 术语和缩略语", "glossary")]},
    {"key": "test-plan", "name": "软件测试计划", "stage": "delivery", "upstream": ["dev-plan", "srs"],
     "description": "规定测试范围、测试环境、测试项与进度。",
     "outline": [(1, "1 范围", None), (1, "2 引用文档", "references"), (1, "3 测试环境", "runtime"), (1, "4 测试项", None),
                 (1, "5 进度安排", None), (1, "6 术语和缩略语", "glossary")]},
    {"key": "user-manual", "name": "软件用户手册", "stage": "delivery", "upstream": ["design"],
     "description": "指导用户安装、配置和使用软件。",
     "outline": [(1, "1 范围", None), (1, "2 引用文档", "references"), (1, "3 软件运行环境", "runtime"), (1, "4 安装与配置", None),
                 (1, "5 使用说明", None), (1, "6 术语和缩略语", "glossary")]},
]

PLAN_KEY = "dev-plan"
_BY_KEY = {item["key"]: item for item in CATALOG}
_BY_NAME = {item["name"]: item for item in CATALOG}
_FRAMEWORK_NAMES = {item["key"]: item["name"] for item in FRAMEWORKS}


def catalog_payload() -> dict:
    return {
        "stages": STAGES,
        "frameworks": FRAMEWORKS,
        "documents": [{
            "key": item["key"], "name": item["name"], "stage": item["stage"], "description": item["description"],
            "upstream": list(item["upstream"]), "chapterCount": len(item["outline"]),
            "outline": [{"level": level, "title": title, "framework": fw} for level, title, fw in item["outline"]],
        } for item in CATALOG],
    }


def match_catalog(name: str, template_name: str | None = None) -> dict | None:
    for candidate in (template_name, name):
        if candidate and candidate in _BY_NAME:
            return _BY_NAME[candidate]
    for item in CATALOG:  # 宽松匹配：项目里常加前缀，如“XX系统软件需求规格说明”
        if name and (item["name"] in name or item["name"].removeprefix("软件") in name):
            return item
    return None


def build_docx(item: dict) -> bytes:
    from docx import Document

    word = Document()
    for level, title, fw in item["outline"]:
        word.add_heading(title, level=level)
        hint = f"（公共框架：{_FRAMEWORK_NAMES[fw]}，各文档引用同一内容。）" if fw else "（在此编写本章内容。）"
        word.add_paragraph(hint)
    buffer = BytesIO()
    word.save(buffer)
    return buffer.getvalue()


def chapter_progress(markdown: str) -> tuple[int, int, list[dict]]:
    """按标题划分章节，章节下有非提示正文即视为已写。返回 (已写, 总数, 章节列表)。"""
    _, blocks = split(markdown or "")
    chapters: list[dict] = []
    for block in blocks:
        if block.kind == "heading":
            chapters.append({"title": heading_text(block.body), "level": block.heading_level, "written": False})
        elif chapters and block.body.strip() and not block.body.strip().startswith("（"):
            chapters[-1]["written"] = True
    return sum(1 for c in chapters if c["written"]), len(chapters), chapters


# ---------------------------------------------------------------- 接口

@router.get("/doc-chain/catalog")
def get_catalog(request: Request) -> dict:
    document_principal(request)
    return {"data": catalog_payload(), "requestId": request.state.request_id}


@router.post("/templates/builtin:seed")
def seed_builtin(request: Request) -> dict:
    """创建（或补齐）内置模板组；已存在的同名模板文档不覆盖。"""
    from app.template_versions import ensure_v1, find_or_store
    from app.template_import import parse_template, validate_structure
    from app.docx_parser import parse_docx
    from app.config import get_settings
    from app.files import resolve_object

    principal = require_permission(request, "templates.manage")
    created = []
    files = {}
    with connect() as conn:  # 文件先落盘（store_bytes 自带连接），再进写事务
        for item in CATALOG:
            files[item["key"]] = find_or_store(conn, build_docx(item), f"{item['name']}.docx")[0]
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM templates WHERE name=%s", [BUILTIN_GROUP]).fetchone()
        if row is None:
            tid = uuid4()
            conn.execute("INSERT INTO templates(id,name,category,description,status) VALUES (%s,%s,%s,%s,'published')",
                         [tid, BUILTIN_GROUP, BUILTIN_CATEGORY, "系统内置的软件研制文档，按依赖链排列。"])
        else:
            tid = row["id"]
        existing = {r["name"] for r in conn.execute("SELECT name FROM template_documents WHERE template_id=%s", [tid]).fetchall()}
        for order, item in enumerate(CATALOG):
            if item["name"] in existing:
                continue
            file_id = files[item["key"]]
            storage = conn.execute("SELECT storage_key FROM files WHERE id=%s", [file_id]).fetchone()
            path = resolve_object(get_settings().file_data_dir, storage["storage_key"])
            parsed = parse_docx(str(path), outline_headings=True)
            structure = parse_template(path)  # 内置模板结构由我们生成，直接视为已核对
            structure = validate_structure(structure, {**structure, "reviewed": True})
            fmt = {**parsed.format_template, "importStructure": structure}
            td = uuid4()
            conn.execute("""INSERT INTO template_documents(id,template_id,name,description,display_order,word_file_id,format_template)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                         [td, tid, item["name"], item["description"], order, file_id, json.dumps(fmt)])
            ensure_v1(conn, td, principal.user_id)
            created.append(item["name"])
        conn.commit()
    return {"data": {"templateId": str(tid), "created": created}, "requestId": request.state.request_id}


def _project_documents(conn, request: Request, project_id: str):
    principal = document_principal(request)
    try:
        pid = UUID(project_id)
    except ValueError:
        raise AppError(404, "NOT_FOUND", "项目不存在或你无权访问。") from None
    from app.projects import _load, project_role
    admin = is_admin(principal) or "auditor" in principal.roles
    _load(conn, pid, principal.user_id, admin)
    role = "owner" if is_admin(principal) else project_role(conn, pid, principal.user_id)
    rows = conn.execute("""SELECT d.id,d.name,d.status,d.markdown_content,d.updated_at,td.name AS template_name,td.id AS td_id,
                                  u.name AS owner_name
                           FROM documents d LEFT JOIN template_documents td ON td.id=d.template_document_id
                           LEFT JOIN users u ON u.id=d.owner_id
                           WHERE d.project_id=%s ORDER BY d.created_at""", [pid]).fetchall()
    return principal, pid, role, rows


def _builtin_template_docs(conn) -> dict[str, str]:
    rows = conn.execute("""SELECT td.id,td.name FROM template_documents td JOIN templates t ON t.id=td.template_id
                           WHERE t.name=%s""", [BUILTIN_GROUP]).fetchall()
    return {r["name"]: str(r["id"]) for r in rows}


@router.get("/projects/{project_id}/doc-chain")
def get_project_chain(request: Request, project_id: str) -> dict:
    with connect() as conn:
        _, _, role, rows = _project_documents(conn, request, project_id)
        templates = _builtin_template_docs(conn)
    nodes, extras, by_key = [], [], {}
    for row in rows:
        item = match_catalog(row["name"], row["template_name"])
        written, total, _ = chapter_progress(row["markdown_content"])
        node = {"documentId": str(row["id"]), "name": row["name"], "status": row["status"], "created": True,
                "written": written, "total": total, "ownerName": row["owner_name"],
                "updatedAt": str(row["updated_at"])}
        if item and item["key"] not in by_key:
            by_key[item["key"]] = node
        else:
            extras.append({**node, "key": None, "stage": None, "upstream": []})
    for item in CATALOG:
        node = by_key.get(item["key"]) or {"documentId": None, "name": item["name"], "status": "missing", "created": False,
                                          "written": 0, "total": len(item["outline"]), "ownerName": None, "updatedAt": None}
        nodes.append({**node, "key": item["key"], "stage": item["stage"], "upstream": list(item["upstream"]),
                      "templateDocumentId": templates.get(item["name"])})
    return {"data": {"stages": STAGES, "nodes": nodes, "others": extras,
                     "canCreate": role == "owner"}, "requestId": request.state.request_id}
