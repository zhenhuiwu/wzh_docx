"""开发计划中的任务分解与公共框架分工（30 §3.4，只读）。

真源是开发计划书“任务分解”“公共框架分工”两章里的表格；本接口只读出并解析到各文档章节。
写回 A07 分工（assignments）与独立任务数据（30 Q-2 默认值）需等 A07 合入、确定迁移编号后再做。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request

from app.db import connect
from app.doc_chain import CATALOG, FRAMEWORKS, PLAN_KEY, _project_documents, match_catalog
from app.markdown_blocks import heading_text, split

router = APIRouter()

_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")


def parse_table(body: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines if not _SEP.match(line)]
    if len(rows) < 2:
        return []
    header = rows[0]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows[1:]]


def section_tables(markdown: str, keyword: str) -> list[dict[str, str]]:
    _, blocks = split(markdown or "")
    inside, level, out = False, 0, []
    for block in blocks:
        if block.kind == "heading":
            if inside and block.heading_level <= level:
                inside = False
            if keyword in heading_text(block.body):
                inside, level = True, block.heading_level
        elif inside and block.kind == "table":
            out.extend(parse_table(block.body))
    return out


def _pick(row: dict[str, str], *names: str) -> str:
    for name in names:
        for key, value in row.items():
            if name in key:
                return value
    return ""


def headings(markdown: str) -> list[dict]:
    _, blocks = split(markdown or "")
    return [{"title": heading_text(b.body), "level": b.heading_level, "index": i}
            for i, b in enumerate(blocks) if b.kind == "heading"]


def resolve_targets(text: str, documents: list[dict]) -> list[dict]:
    """“软件需求规格说明 3.2、3.4；软件开发计划 4” → 每项一个章节。未找到的保留并标记 found=False。"""
    out = []
    for part in re.split(r"[;；\n]", text):
        part = part.strip()
        if not part:
            continue
        doc = next((d for d in sorted(documents, key=lambda d: -len(d["name"])) if d["name"] in part), None)
        if doc is None:
            doc = next((d for d in documents if d["catalogName"] and d["catalogName"].removeprefix("软件") in part), None)
        rest = part.replace(doc["name"], "") if doc else part
        for ref in [r.strip() for r in re.split(r"[,，、]", rest) if r.strip(" ›>")]:
            ref = ref.strip(" ›>")
            heading = None
            if doc:
                heading = next((h for h in doc["headings"] if h["title"] == ref or h["title"].split(" ")[0] == ref
                                or h["title"].startswith(ref + " ")), None)
            out.append({"documentId": doc["id"] if doc else None, "documentName": doc["name"] if doc else None,
                        "chapter": heading["title"] if heading else ref, "found": heading is not None})
        if doc and not rest.strip(" ,，、›>"):
            out.append({"documentId": doc["id"], "documentName": doc["name"], "chapter": None, "found": True})
    return out


@router.get("/projects/{project_id}/plan-tasks")
def get_plan_tasks(request: Request, project_id: str) -> dict:
    with connect() as conn:
        principal, pid, role, rows = _project_documents(conn, request, project_id)
        members = {r["name"]: str(r["id"]) for r in conn.execute(
            "SELECT u.id,u.name FROM users u WHERE u.id IN (SELECT user_id FROM project_members WHERE project_id=%s) "
            "OR u.id=(SELECT owner_id FROM projects WHERE id=%s)", [pid, pid]).fetchall()}
    documents = []
    plan = None
    for row in rows:
        item = match_catalog(row["name"], row["template_name"])
        doc = {"id": str(row["id"]), "name": row["name"], "catalogKey": item["key"] if item else None,
               "catalogName": item["name"] if item else None, "headings": headings(row["markdown_content"]),
               "markdown": row["markdown_content"]}
        documents.append(doc)
        if plan is None and doc["catalogKey"] == PLAN_KEY:
            plan = doc
    me = str(principal.user_id)
    if plan is None:
        return {"data": {"planDocument": None, "tasks": [], "frameworks": [], "canManage": role == "owner"},
                "requestId": request.state.request_id}

    def person(name: str) -> dict:
        uid = members.get(name)
        return {"name": name or None, "userId": uid, "isMe": uid == me}

    tasks = []
    for row in section_tables(plan["markdown"], "任务分解"):
        title = _pick(row, "任务", "名称")
        if not title:
            continue
        targets = resolve_targets(_pick(row, "章节", "涉及", "文档"), documents)
        tasks.append({"title": title, "assignee": person(_pick(row, "负责", "分配")),
                      "targets": targets, "documentCount": len({t["documentId"] for t in targets if t["documentId"]})})
    assigned = {_pick(r, "框架", "名称"): _pick(r, "负责", "分配") for r in section_tables(plan["markdown"], "公共框架")}
    frameworks = []
    for fw in FRAMEWORKS:
        refs = []
        for doc in documents:
            item = next((c for c in CATALOG if c["key"] == doc["catalogKey"]), None)
            for _level, title, key in (item["outline"] if item else []):
                if key == fw["key"]:
                    refs.append({"documentId": doc["id"], "documentName": doc["name"], "chapter": title})
        frameworks.append({"key": fw["key"], "name": fw["name"], "assignee": person(assigned.get(fw["name"], "")), "references": refs})
    return {"data": {"planDocument": {"id": plan["id"], "name": plan["name"]}, "tasks": tasks, "frameworks": frameworks,
                     "canManage": role == "owner"}, "requestId": request.state.request_id}
