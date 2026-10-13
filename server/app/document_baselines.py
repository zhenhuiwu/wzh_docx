"""文档基线：git 式的建立、历史、对比、回退（30 §3.5）。

- 建立：把当前正文按章节（Markdown 标题）切块，章节正文按 sha256 去重存储。
- 历史：分页列出，最新在前。
- 对比：两个基线，或基线与当前正文；先按章节对齐，再对改动章节做段落级对比，结果分页。
- 回退：把正文恢复到某个基线，经由常规保存通道（产生新文档修订），并记一条 kind=revert
  的新基线，旧基线不删、不改。
建立基线、回退只有项目负责人和系统管理员能做；成员可读历史和对比。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.db import connect
from app.document_access import document_principal
from app.errors import AppError
from app.models import format_timestamp

router = APIRouter()

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_MAX_PAGE = 200


class BaselineCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=500)


class BaselineRevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    message: str = Field(default="", max_length=500)


# ---------- 切块 ----------

def split_sections(markdown: str) -> list[dict]:
    """按标题切章节；首个标题前的内容为 level 0 的前言章节。代码块里的 # 不算标题。"""
    sections: list[dict] = []
    lines: list[str] = []
    heading, level, fenced = "", 0, False

    def flush():
        text = "\n".join(lines)
        if sections or text.strip():
            sections.append({"heading": heading, "level": level, "content": text})

    for line in markdown.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            flush()
            lines, heading, level = [line], match.group(2), len(match.group(1))
        else:
            lines.append(line)
    flush()
    return sections


def join_sections(contents: list[str]) -> str:
    return "\n".join(contents)


def split_paragraphs(content: str) -> list[str]:
    return [part.strip("\n") for part in re.split(r"\n\s*\n", content) if part.strip()]


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------- 访问 ----------

def _load(connection, request: Request, document_id: str, need: str = "read"):
    from app.documents import _admin, _document_row, _uuid
    actor = document_principal(request)
    did = _uuid(document_id, "documentId")
    return did, actor, _document_row(connection, did, actor.user_id, _admin(actor.roles), need)


def _require_manager(row) -> None:
    if not (row.admin or row.project_role == "owner"):
        raise AppError(403, "FORBIDDEN", "只有项目负责人和管理员可以建立基线或回退。",
                       details={"projectRole": row.project_role, "ownerName": row.owner_name})
    if row.archived:
        raise AppError(409, "PROJECT_ARCHIVED", "项目已归档，文档只读。")


def _json(value, default):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _public(row) -> dict:
    return {
        "id": str(row["id"]), "seq": row["seq"], "label": f"v{row['seq']}", "message": row["message"],
        "kind": row["kind"], "revertedFromSeq": row["reverted_from_seq"], "revision": row["revision"],
        "sectionCount": row["section_count"], "paragraphCount": row["paragraph_count"],
        "createdAt": format_timestamp(row["created_at"]),
        "author": None if row["author_id"] is None else {"id": str(row["author_id"]), "name": row["author_name"]},
    }


_BASE_SELECT = """SELECT b.*, u.name AS author_name FROM document_baselines b
                  LEFT JOIN users u ON u.id = b.author_id"""


def _baseline(connection, did: UUID, seq: int):
    row = connection.execute(f"{_BASE_SELECT} WHERE b.document_id=%s AND b.seq=%s", (did, seq)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "找不到这个基线。")
    return row


def _sections_of(connection, baseline_id) -> list[dict]:
    rows = connection.execute(
        """SELECT s.position, s.hash, s.heading, s.level, bl.body FROM document_baseline_sections s
           JOIN document_baseline_blobs bl ON bl.hash = s.hash WHERE s.baseline_id=%s ORDER BY s.position""",
        (baseline_id,)).fetchall()
    return [{"hash": r["hash"], "heading": r["heading"], "level": r["level"], "content": r["body"]} for r in rows]


def _current_sections(markdown: str) -> list[dict]:
    return [{**s, "hash": _hash(s["content"])} for s in split_sections(markdown or "")]


# ---------- 写入 ----------

def create_baseline(connection, did: UUID, author_id, message: str, *, kind: str = "create",
                    reverted_from_seq: int | None = None) -> dict:
    """调用方负责事务和 commit。"""
    doc = connection.execute("SELECT revision, markdown_content, outline, format_template FROM documents WHERE id=%s",
                             (did,)).fetchone()
    sections = _current_sections(doc["markdown_content"])
    paragraphs = 0
    for section in sections:
        count = len(split_paragraphs(section["content"]))
        paragraphs += count
        connection.execute("INSERT INTO document_baseline_blobs(hash, body, paragraph_count) VALUES (%s,%s,%s) "
                           "ON CONFLICT(hash) DO NOTHING", (section["hash"], section["content"], count))
    seq = connection.execute("SELECT coalesce(max(seq),0)+1 AS n FROM document_baselines WHERE document_id=%s",
                             (did,)).fetchone()["n"]
    bid = uuid4()
    connection.execute(
        """INSERT INTO document_baselines(id, document_id, seq, message, author_id, kind, reverted_from_seq, revision,
           format_template, outline, section_count, paragraph_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (bid, did, seq, message.strip(), author_id, kind, reverted_from_seq, doc["revision"],
         json.dumps(_json(doc["format_template"], {}), ensure_ascii=False),
         json.dumps(_json(doc["outline"], []), ensure_ascii=False), len(sections), paragraphs))
    for position, section in enumerate(sections):
        connection.execute("INSERT INTO document_baseline_sections(baseline_id, position, hash, heading, level) "
                           "VALUES (%s,%s,%s,%s,%s)", (bid, position, section["hash"], section["heading"], section["level"]))
    return _public(connection.execute(f"{_BASE_SELECT} WHERE b.id=%s", (bid,)).fetchone())


# ---------- 对比 ----------

def diff_sections(old: list[dict], new: list[dict]) -> tuple[list[dict], dict]:
    """章节按 (标题, 哈希) 对齐；同位替换的章节再做段落级对比。"""
    changes: list[dict] = []
    stats = {"added": 0, "removed": 0, "modified": 0, "paragraphsAdded": 0, "paragraphsRemoved": 0}

    def brief(section):
        return {"heading": section["heading"], "level": section["level"]}

    def add(kind, a=None, b=None):
        item = {"type": kind, "old": None if a is None else brief(a), "new": None if b is None else brief(b)}
        if kind == "added":
            paras = split_paragraphs(b["content"])
            item["paragraphs"] = [{"op": "insert", "new": p} for p in paras]
            stats["added"] += 1
            stats["paragraphsAdded"] += len(paras)
        elif kind == "removed":
            paras = split_paragraphs(a["content"])
            item["paragraphs"] = [{"op": "delete", "old": p} for p in paras]
            stats["removed"] += 1
            stats["paragraphsRemoved"] += len(paras)
        else:
            item["paragraphs"] = diff_paragraphs(a["content"], b["content"], stats)
            if not item["paragraphs"] and a["heading"] == b["heading"] and a["level"] == b["level"]:
                return  # 只差空行，不算改动
            stats["modified"] += 1
        changes.append(item)

    keys_a = [s["hash"] for s in old]
    keys_b = [s["hash"] for s in new]
    matcher = difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        a_part, b_part = old[i1:i2], new[j1:j2]
        # 同名章节视为修改，其余为增删
        inner = difflib.SequenceMatcher(None, [s["heading"] for s in a_part], [s["heading"] for s in b_part], autojunk=False)
        for op2, x1, x2, y1, y2 in inner.get_opcodes():
            if op2 == "equal":
                for a, b in zip(a_part[x1:x2], b_part[y1:y2]):
                    add("modified", a, b)
            elif op2 == "replace" and x2 - x1 == y2 - y1:
                for a, b in zip(a_part[x1:x2], b_part[y1:y2]):
                    add("modified", a, b)
            else:
                for a in a_part[x1:x2]:
                    add("removed", a=a)
                for b in b_part[y1:y2]:
                    add("added", b=b)
    return changes, stats


def diff_paragraphs(old: str, new: str, stats: dict) -> list[dict]:
    a, b = split_paragraphs(old), split_paragraphs(new)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op == "replace" and i2 - i1 == j2 - j1:
            out.extend({"op": "replace", "old": x, "new": y} for x, y in zip(a[i1:i2], b[j1:j2]))
            stats["paragraphsAdded"] += j2 - j1
            stats["paragraphsRemoved"] += i2 - i1
            continue
        out.extend({"op": "delete", "old": x} for x in a[i1:i2])
        out.extend({"op": "insert", "new": y} for y in b[j1:j2])
        stats["paragraphsRemoved"] += i2 - i1
        stats["paragraphsAdded"] += j2 - j1
    return out


# ---------- 接口 ----------

@router.post("/documents/{document_id}/baselines", status_code=201)
def post_baseline(request: Request, document_id: str, body: BaselineCreateBody) -> dict:
    with connect() as connection:
        did, actor, row = _load(connection, request, document_id)
        _require_manager(row)
        result = create_baseline(connection, did, actor.user_id, body.message)
        connection.commit()
    return {"data": result, "requestId": request.state.request_id}


@router.get("/documents/{document_id}/baselines")
def list_baselines(request: Request, document_id: str, offset: int = 0, limit: int = 20) -> dict:
    if offset < 0 or not 1 <= limit <= _MAX_PAGE:
        raise AppError(422, "VALIDATION_FAILED", "基线查询范围不正确。")
    with connect() as connection:
        did, _, row = _load(connection, request, document_id)
        rows = connection.execute(f"{_BASE_SELECT} WHERE b.document_id=%s ORDER BY b.seq DESC LIMIT %s OFFSET %s",
                                  (did, limit + 1, offset)).fetchall()
        total = connection.execute("SELECT count(*) AS n FROM document_baselines WHERE document_id=%s", (did,)).fetchone()["n"]
    return {"data": {"items": [_public(r) for r in rows[:limit]], "offset": offset, "limit": limit,
                     "hasMore": len(rows) > limit, "total": total, "currentRevision": row.revision},
            "requestId": request.state.request_id}


@router.get("/documents/{document_id}/baselines/{seq}")
def get_baseline(request: Request, document_id: str, seq: int, content: bool = False) -> dict:
    with connect() as connection:
        did, _, _ = _load(connection, request, document_id)
        base = _baseline(connection, did, seq)
        result = _public(base)
        sections = _sections_of(connection, base["id"])
        result["sections"] = [{"heading": s["heading"], "level": s["level"]} for s in sections]
        if content:
            result["markdownContent"] = join_sections([s["content"] for s in sections])
    return {"data": result, "requestId": request.state.request_id}


@router.get("/documents/{document_id}/baselines-diff")
def diff_baselines(request: Request, document_id: str, base: int, compare: str = "current",
                   offset: int = 0, limit: int = 50) -> dict:
    """base=基线序号；compare=另一个基线序号或 current（当前正文）。改动按章节分页。"""
    if offset < 0 or not 1 <= limit <= _MAX_PAGE:
        raise AppError(422, "VALIDATION_FAILED", "对比范围不正确。")
    with connect() as connection:
        did, _, row = _load(connection, request, document_id)
        old = _sections_of(connection, _baseline(connection, did, base)["id"])
        if compare == "current":
            new = _current_sections(row.markdown_content)
        else:
            try:
                compare_seq = int(compare)
            except ValueError as exc:
                raise AppError(422, "VALIDATION_FAILED", "compare 只能是基线序号或 current。") from exc
            new = _sections_of(connection, _baseline(connection, did, compare_seq)["id"])
    changes, stats = diff_sections(old, new)
    return {"data": {"base": base, "compare": compare, "stats": stats, "total": len(changes),
                     "items": changes[offset:offset + limit], "offset": offset, "limit": limit,
                     "hasMore": offset + limit < len(changes)}, "requestId": request.state.request_id}


@router.post("/documents/{document_id}/baselines/{seq}/revert")
def revert_baseline(request: Request, document_id: str, seq: int, body: BaselineRevertBody) -> dict:
    from app.document_objects import revision_conflict
    from app.documents import _check_word, _save_markdown
    with connect() as connection:
        did, actor, row = _load(connection, request, document_id, "edit")
        _require_manager(row)
        if row.revision != body.expectedRevision:
            raise revision_conflict(connection, did, "文档已更新，请重新核对后回退。")
        target = _baseline(connection, did, seq)
        markdown = join_sections([s["content"] for s in _sections_of(connection, target["id"])])
        template = _json(target["format_template"], {})
        _check_word(connection, row, markdown, images=template.get("images", []))
        _save_markdown(connection, did, markdown, body.expectedRevision, format_template=template,
                       outline=_json(target["outline"], []), kind="restore", restored_from_revision=target["revision"])
        message = body.message.strip() or f"回退到 B{seq}"
        result = create_baseline(connection, did, actor.user_id, message, kind="revert", reverted_from_seq=seq)
        connection.commit()
    return {"data": result, "requestId": request.state.request_id}
