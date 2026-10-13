"""任务分解（独立数据，30 Q-2）与公共框架块（30 §3.4、Q-6）。

任务 → 多份文档的章节（标题对象）→ 负责人；保存时写回 A07 显式分工（document_object_assignments），
硬限制逻辑仍由 A07 负责。删除任务或移除章节时，只撤销仍由该任务负责人持有的显式分配，章节恢复默认。
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app import document_assignments as da
from app.db import connect, write_transaction
from app.doc_chain import CATALOG, FRAMEWORKS, PLAN_KEY, chapter_key, match_catalog
from app.document_access import document_principal, ensure_project_writable, is_admin, load_document
from app.errors import AppError
from app.markdown_blocks import heading_text

router = APIRouter()


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentId: str
    objectId: str


class TaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    assigneeId: str | None = None
    targets: list[Target] = Field(default_factory=list, max_length=200)


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    assigneeId: str | None = None
    clearAssignee: bool = False
    targets: list[Target] | None = Field(default=None, max_length=200)


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(ge=0)
    body: str = Field(max_length=200_000)


class PublicAssignee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assigneeId: str | None = None


def _id(value: str, message: str = "对象不存在或你无权访问。") -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise AppError(404, "NOT_FOUND", message) from None


def _project(conn, request: Request, project_id: str):
    from app.projects import _load, project_role
    principal = document_principal(request)
    pid = _id(project_id, "项目不存在或你无权访问。")
    _load(conn, UUID(pid), principal.user_id, is_admin(principal) or "auditor" in principal.roles)
    role = "owner" if is_admin(principal) else project_role(conn, UUID(pid), principal.user_id)
    return principal, pid, role


def _require_owner(conn, pid: str, role: str | None) -> None:
    if role != "owner":
        raise AppError(403, "FORBIDDEN", "只有项目负责人和系统管理员可以分配任务。")
    ensure_project_writable(conn, UUID(pid))


def _check_assignee(conn, pid: str, uid: str | None) -> None:
    if uid is None:
        return
    row = conn.execute("""SELECT u.status, m.role FROM users u LEFT JOIN project_members m ON m.user_id=u.id AND m.project_id=%s
                          WHERE u.id=%s""", [pid, uid]).fetchone()
    if row is None or row["role"] not in ("owner", "editor") or row["status"] != "active":
        raise AppError(422, "ASSIGNEE_NOT_ELIGIBLE", "只能分配给本项目已启用的负责人或编写人员。",
                       field_errors=[{"field": "assigneeId", "message": "请选择项目里的负责人或编写人员。"}])


def _check_targets(conn, pid: str, targets: list[Target]) -> list[tuple[str, str]]:
    out = []
    for t in targets:
        did, oid = _id(t.documentId), _id(t.objectId)
        doc = conn.execute("SELECT 1 FROM documents WHERE id=%s AND project_id=%s", [did, pid]).fetchone()
        obj = conn.execute("SELECT kind FROM document_objects WHERE id=%s AND document_id=%s AND deleted_at IS NULL", [oid, did]).fetchone()
        if doc is None or obj is None or obj["kind"] != "heading":
            raise AppError(409, "OBJECT_STALE", "所选章节已被删除或合并，请重新选择。", details={"documentId": did, "objectId": oid})
        if (did, oid) not in out:
            out.append((did, oid))
    return out


def _write_back(conn, actor, pairs: list[tuple[str, str]], assignee: str | None, previous: str | None, remove: list[tuple[str, str]]) -> None:
    """写回 A07：pairs 设为 assignee；remove 中仍由 previous 单独持有的显式分配撤销。每份文档 bump 一次并记事件。"""
    touched: dict[str, tuple[list[str], list[str]]] = {}
    for did, oid in pairs:
        conn.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s", [did, oid])
        if assignee:
            conn.execute("""INSERT INTO document_object_assignments(document_id,object_id,assignee_id,source,needs_confirm,assigned_by,assigned_at)
                            VALUES (%s,%s,%s,'manual',0,%s,now())""", [did, oid, assignee, str(actor)])
        touched.setdefault(did, ([], []))[0].append(oid)
    for did, oid in remove:
        if not previous:
            continue
        held = {str(r["assignee_id"]) for r in conn.execute(
            "SELECT assignee_id FROM document_object_assignments WHERE document_id=%s AND object_id=%s", [did, oid]).fetchall()}
        if held == {previous}:
            conn.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s", [did, oid])
            touched.setdefault(did, ([], []))[1].append(oid)
    for did, (assigned, removed) in touched.items():
        da.bump(conn, did)
        if assigned:
            da.event(conn, did, "assignment", "assign" if assignee else "unassign", assigned, None, actor,
                     payload={"assigneeIds": [assignee] if assignee else [], "via": "task"})
        if removed:
            da.event(conn, did, "assignment", "unassign", removed, None, actor, payload={"assigneeIds": [], "via": "task"})


def _targets_of(conn, task_id: str) -> list[tuple[str, str]]:
    return [(str(r["document_id"]), str(r["object_id"])) for r in conn.execute(
        "SELECT document_id, object_id FROM project_task_targets WHERE task_id=%s ORDER BY rowid", [task_id]).fetchall()]


def _task_payload(conn, row, me: str) -> dict:
    targets = []
    for did, oid in _targets_of(conn, str(row["id"])):
        doc = conn.execute("SELECT name FROM documents WHERE id=%s", [did]).fetchone()
        obj = conn.execute("SELECT body, deleted_at FROM document_objects WHERE id=%s", [oid]).fetchone()
        owners = [r["name"] for r in conn.execute("""SELECT u.name FROM document_object_assignments a JOIN users u ON u.id=a.assignee_id
                                                    WHERE a.document_id=%s AND a.object_id=%s ORDER BY u.name""", [did, oid]).fetchall()]
        targets.append({"documentId": did, "documentName": doc["name"] if doc else None, "objectId": oid,
                        "chapter": heading_text(obj["body"]) if obj else None, "stale": obj is None or obj["deleted_at"] is not None,
                        "owners": owners})
    assignee = None if row["assignee_id"] is None else str(row["assignee_id"])
    return {"id": str(row["id"]), "title": row["title"], "description": row["description"], "revision": row["revision"],
            "assignee": {"id": assignee, "name": row["assignee_name"], "isMe": assignee == me},
            "targets": targets, "documentCount": len({t["documentId"] for t in targets})}


def _task_row(conn, task_id: str):
    return conn.execute("""SELECT t.*, u.name AS assignee_name FROM project_tasks t LEFT JOIN users u ON u.id=t.assignee_id
                           WHERE t.id=%s""", [task_id]).fetchone()


# ---------------------------------------------------------------- 公共框架块

def framework_refs(conn, pid: str) -> dict[str, list[dict]]:
    """各文档中对应公共框架的章节：按内置目录中标记为公共框架的章节标题匹配。"""
    titles: dict[str, str] = {}
    for item in CATALOG:
        for _level, title, key in item["outline"]:
            if key:
                titles[chapter_key(title)] = key
    for fw in FRAMEWORKS:
        titles.setdefault(chapter_key(fw["name"]), fw["key"])
    titles.setdefault("运行环境", "runtime")
    refs: dict[str, list[dict]] = {}
    for doc in conn.execute("SELECT id, name FROM documents WHERE project_id=%s ORDER BY created_at", [pid]).fetchall():
        for obj in conn.execute("""SELECT id, body FROM document_objects WHERE document_id=%s AND kind='heading' AND deleted_at IS NULL
                                   ORDER BY position""", [str(doc["id"])]).fetchall():
            title = heading_text(obj["body"])
            key = titles.get(chapter_key(title))
            if key:
                refs.setdefault(key, []).append({"documentId": str(doc["id"]), "documentName": doc["name"],
                                                 "objectId": str(obj["id"]), "chapter": title})
    return refs


def ensure_public_blocks(conn, pid: str) -> None:
    for fw in FRAMEWORKS:
        conn.execute("INSERT OR IGNORE INTO public_blocks(id,project_id,framework_key,title) VALUES (%s,%s,%s,%s)",
                     [str(uuid4()), pid, fw["key"], fw["name"]])


def _public_rows(conn, pid: str):
    return conn.execute("""SELECT b.*, u.name AS assignee_name, w.name AS updated_by_name FROM public_blocks b
                           LEFT JOIN users u ON u.id=b.assignee_id LEFT JOIN users w ON w.id=b.updated_by
                           WHERE b.project_id=%s ORDER BY b.rowid""", [pid]).fetchall()


def _public_payload(row, refs: list[dict], me: str, role: str | None) -> dict:
    assignee = None if row["assignee_id"] is None else str(row["assignee_id"])
    return {"id": str(row["id"]), "key": row["framework_key"], "name": row["title"], "body": row["body"], "revision": row["revision"],
            "assignee": {"id": assignee, "name": row["assignee_name"], "isMe": assignee == me},
            "updatedBy": row["updated_by_name"], "updatedAt": str(row["updated_at"]),
            "canEdit": role == "owner" or (role == "editor" and assignee == me), "references": refs}


# ---------------------------------------------------------------- 接口：任务

@router.get("/projects/{project_id}/tasks")
def list_tasks(request: Request, project_id: str) -> dict:
    with connect() as conn:
        principal, pid, role = _project(conn, request, project_id)
        me = str(principal.user_id)
        with write_transaction(conn):
            ensure_public_blocks(conn, pid)
        tasks = [_task_payload(conn, row, me) for row in conn.execute(
            """SELECT t.*, u.name AS assignee_name FROM project_tasks t LEFT JOIN users u ON u.id=t.assignee_id
               WHERE t.project_id=%s ORDER BY t.display_order, t.created_at""", [pid]).fetchall()]
        refs = framework_refs(conn, pid)
        frameworks = [_public_payload(r, refs.get(r["framework_key"], []), me, role) for r in _public_rows(conn, pid)]
        plan = None
        for doc in conn.execute("""SELECT d.id, d.name, td.name AS tname FROM documents d LEFT JOIN template_documents td
                                   ON td.id=d.template_document_id WHERE d.project_id=%s ORDER BY d.created_at""", [pid]).fetchall():
            item = match_catalog(doc["name"], doc["tname"])
            if item and item["key"] == PLAN_KEY:
                plan = {"id": str(doc["id"]), "name": doc["name"]}
                break
        members = [{"id": str(r["id"]), "name": r["name"]} for r in conn.execute(
            """SELECT u.id, u.name FROM project_members m JOIN users u ON u.id=m.user_id
               WHERE m.project_id=%s AND m.role IN ('owner','editor') AND u.status='active' ORDER BY u.name""", [pid]).fetchall()]
        documents = [{"id": str(r["id"]), "name": r["name"]} for r in conn.execute(
            "SELECT id, name FROM documents WHERE project_id=%s ORDER BY created_at", [pid]).fetchall()]
    return {"data": {"tasks": tasks, "frameworks": frameworks, "planDocument": plan, "members": members, "documents": documents,
                     "canManage": role == "owner"}, "requestId": request.state.request_id}


@router.get("/projects/{project_id}/documents/{document_id}/chapters")
def picker_chapters(request: Request, project_id: str, document_id: str) -> dict:
    """BlockPicker 右侧章节树：标题对象 + 当前显式负责人。"""
    with connect() as conn:
        principal, pid, _role = _project(conn, request, project_id)
        did = _id(document_id)
        doc = load_document(conn, UUID(did), principal.user_id, is_admin(principal))
        if str(doc.project_id) != pid:
            raise AppError(404, "NOT_FOUND", "文档不存在或你无权访问。")
        from app.document_objects import ensure_committed
        ensure_committed(conn, UUID(did))
        explicit = da.explicit_map(conn, did)
        names = da._names(conn, {v for vs in explicit.values() for v in vs})
        tasks = {(str(r["object_id"])): r["title"] for r in conn.execute(
            """SELECT g.object_id, t.title FROM project_task_targets g JOIN project_tasks t ON t.id=g.task_id
               WHERE g.document_id=%s""", [did]).fetchall()}
        chapters = [{"objectId": n.id, "level": int(n.level or 1), "title": heading_text(n.body),
                     "owners": [{"id": v, "name": names.get(v, "")} for v in sorted(explicit.get(n.id, ()))],
                     "task": tasks.get(n.id)}
                    for n in da.nodes_of(conn, did) if n.kind == "heading"]
    return {"data": {"documentId": did, "chapters": chapters}, "requestId": request.state.request_id}


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(request: Request, project_id: str, body: TaskBody) -> dict:
    with connect() as conn:
        principal, pid, role = _project(conn, request, project_id)
        _require_owner(conn, pid, role)
        assignee = _id(body.assigneeId) if body.assigneeId else None
        with write_transaction(conn):
            _check_assignee(conn, pid, assignee)
            pairs = _check_targets(conn, pid, body.targets)
            tid = str(uuid4())
            order = conn.execute("SELECT COALESCE(MAX(display_order),0)+1 AS n FROM project_tasks WHERE project_id=%s", [pid]).fetchone()["n"]
            conn.execute("""INSERT INTO project_tasks(id,project_id,title,description,assignee_id,display_order,created_by)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""", [tid, pid, body.title.strip(), body.description, assignee, order, str(principal.user_id)])
            for did, oid in pairs:
                conn.execute("INSERT INTO project_task_targets(task_id,document_id,object_id) VALUES (%s,%s,%s)", [tid, did, oid])
            if assignee:
                _write_back(conn, principal.user_id, pairs, assignee, None, [])
        return {"data": _task_payload(conn, _task_row(conn, tid), str(principal.user_id)), "requestId": request.state.request_id}


def _load_task(conn, request: Request, task_id: str):
    tid = _id(task_id, "任务不存在。")
    row = conn.execute("SELECT project_id FROM project_tasks WHERE id=%s", [tid]).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "任务不存在。")
    principal, pid, role = _project(conn, request, str(row["project_id"]))
    return principal, pid, role, tid


@router.patch("/tasks/{task_id}")
def update_task(request: Request, task_id: str, body: TaskPatch) -> dict:
    with connect() as conn:
        principal, pid, role, tid = _load_task(conn, request, task_id)
        _require_owner(conn, pid, role)
        with write_transaction(conn):
            row = _task_row(conn, tid)
            if row["revision"] != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "任务刚被修改过，已显示最新。", details={"revision": row["revision"]})
            previous = None if row["assignee_id"] is None else str(row["assignee_id"])
            assignee = None if body.clearAssignee else (_id(body.assigneeId) if body.assigneeId else previous)
            _check_assignee(conn, pid, assignee)
            old = _targets_of(conn, tid)
            new = _check_targets(conn, pid, body.targets) if body.targets is not None else old
            removed = [p for p in old if p not in new]
            changed = new if assignee != previous else [p for p in new if p not in old]
            conn.execute("""UPDATE project_tasks SET title=COALESCE(%s,title), description=COALESCE(%s,description), assignee_id=%s,
                            revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                         [body.title.strip() if body.title else None, body.description, assignee, tid])
            conn.execute("DELETE FROM project_task_targets WHERE task_id=%s", [tid])
            for did, oid in new:
                conn.execute("INSERT INTO project_task_targets(task_id,document_id,object_id) VALUES (%s,%s,%s)", [tid, did, oid])
            if assignee is None and previous:
                _write_back(conn, principal.user_id, [], None, previous, old)  # 取消负责人：恢复章节默认
            else:
                _write_back(conn, principal.user_id, changed if assignee else [], assignee, previous, removed)
        return {"data": _task_payload(conn, _task_row(conn, tid), str(principal.user_id)), "requestId": request.state.request_id}


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(request: Request, task_id: str, expectedRevision: int) -> None:
    """删除任务不删正文；涉及章节的分工恢复为章节默认（只撤销仍由该任务负责人持有的显式分配）。"""
    with connect() as conn:
        _principal, pid, role, tid = _load_task(conn, request, task_id)
        _require_owner(conn, pid, role)
        with write_transaction(conn):
            row = _task_row(conn, tid)
            if row["revision"] != expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "任务刚被修改过，已显示最新。", details={"revision": row["revision"]})
            previous = None if row["assignee_id"] is None else str(row["assignee_id"])
            _write_back(conn, _principal.user_id, [], None, previous, _targets_of(conn, tid))
            conn.execute("DELETE FROM project_tasks WHERE id=%s", [tid])


@router.get("/projects/{project_id}/tasks:markdown")
def tasks_markdown(request: Request, project_id: str) -> dict:
    """开发计划书“任务分解”章节的生成内容（Q-2：由任务数据生成，只读）。"""
    data = list_tasks(request, project_id)["data"]
    lines = ["| 任务 | 负责人 | 涉及章节 |", "| --- | --- | --- |"]
    for t in data["tasks"]:
        where = "；".join(f"{x['documentName']} {x['chapter']}" for x in t["targets"] if x["documentName"])
        lines.append(f"| {t['title']} | {t['assignee']['name'] or '未分配'} | {where or '—'} |")
    return {"data": {"markdown": "\n".join(lines) + "\n"}, "requestId": request.state.request_id}


# ---------------------------------------------------------------- 接口：公共框架

def _load_public(conn, request: Request, block_id: str):
    bid = _id(block_id, "公共内容不存在。")
    row = conn.execute("SELECT project_id FROM public_blocks WHERE id=%s", [bid]).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "公共内容不存在。")
    principal, pid, role = _project(conn, request, str(row["project_id"]))
    return principal, pid, role, bid


def _one_public(conn, pid, bid, me, role):
    row = next(r for r in _public_rows(conn, pid) if str(r["id"]) == bid)
    return _public_payload(row, framework_refs(conn, pid).get(row["framework_key"], []), me, role)


@router.put("/public-blocks/{block_id}")
def save_public(request: Request, block_id: str, body: PublicBody) -> dict:
    """公共内容只在这里修改（Q-6：立即生效，引用处显示“公共内容有更新”）。"""
    with connect() as conn:
        principal, pid, role, bid = _load_public(conn, request, block_id)
        me = str(principal.user_id)
        ensure_project_writable(conn, UUID(pid))
        with write_transaction(conn):
            row = conn.execute("SELECT revision, assignee_id FROM public_blocks WHERE id=%s", [bid]).fetchone()
            if not (role == "owner" or (role == "editor" and row["assignee_id"] is not None and str(row["assignee_id"]) == me)):
                raise AppError(403, "ASSIGNMENT_FORBIDDEN", "公共内容只能由它的负责人或项目负责人修改。")
            if row["revision"] != body.expectedRevision:
                raise AppError(409, "REVISION_CONFLICT", "公共内容刚被修改过，已显示最新。", details={"revision": row["revision"]})
            conn.execute("""UPDATE public_blocks SET body=%s, revision=revision+1, updated_by=%s, updated_at=CURRENT_TIMESTAMP
                            WHERE id=%s""", [body.body, me, bid])
        return {"data": _one_public(conn, pid, bid, me, role), "requestId": request.state.request_id}


@router.put("/public-blocks/{block_id}/assignee")
def assign_public(request: Request, block_id: str, body: PublicAssignee) -> dict:
    with connect() as conn:
        principal, pid, role, bid = _load_public(conn, request, block_id)
        _require_owner(conn, pid, role)
        uid = _id(body.assigneeId) if body.assigneeId else None
        with write_transaction(conn):
            _check_assignee(conn, pid, uid)
            conn.execute("UPDATE public_blocks SET assignee_id=%s WHERE id=%s", [uid, bid])
        return {"data": _one_public(conn, pid, bid, str(principal.user_id), role), "requestId": request.state.request_id}


@router.get("/documents/{document_id}/public-refs")
def document_public_refs(request: Request, document_id: str) -> dict:
    """工作台用：本文档里引用公共内容的章节，带“有更新”标记（PublicBlockEmbed 数据源）。"""
    principal = document_principal(request)
    did = _id(document_id, "文档不存在或你无权访问。")
    with connect() as conn:
        doc = load_document(conn, UUID(did), principal.user_id, is_admin(principal))
        pid = str(doc.project_id)
        with write_transaction(conn):
            ensure_public_blocks(conn, pid)
        role = "owner" if doc.admin else doc.project_role
        refs = framework_refs(conn, pid)
        out = []
        for row in _public_rows(conn, pid):
            mine = [r for r in refs.get(row["framework_key"], []) if r["documentId"] == did]
            if not mine:
                continue
            seen = conn.execute("SELECT seen_revision FROM public_block_refs WHERE document_id=%s AND public_block_id=%s",
                                [did, str(row["id"])]).fetchone()
            seen_rev = seen["seen_revision"] if seen else 0
            block = _public_payload(row, [], str(principal.user_id), role)
            for ref in mine:
                out.append({"objectId": ref["objectId"], "chapter": ref["chapter"], "block": block,
                            "seenRevision": seen_rev, "updated": row["revision"] > seen_rev and row["revision"] > 0})
    return {"data": {"references": out}, "requestId": request.state.request_id}


@router.post("/documents/{document_id}/public-refs/{block_id}:seen")
def mark_public_seen(request: Request, document_id: str, block_id: str) -> dict:
    principal = document_principal(request)
    did, bid = _id(document_id), _id(block_id)
    with connect() as conn:
        doc = load_document(conn, UUID(did), principal.user_id, is_admin(principal))
        with write_transaction(conn):
            row = conn.execute("SELECT revision FROM public_blocks WHERE id=%s AND project_id=%s", [bid, str(doc.project_id)]).fetchone()
            if row is None:
                raise AppError(404, "NOT_FOUND", "公共内容不存在。")
            conn.execute("""INSERT INTO public_block_refs(document_id,public_block_id,seen_revision) VALUES (%s,%s,%s)
                            ON CONFLICT(document_id,public_block_id) DO UPDATE SET seen_revision=excluded.seen_revision""",
                         [did, bid, row["revision"]])
    return {"data": {"seenRevision": row["revision"]}, "requestId": request.state.request_id}
