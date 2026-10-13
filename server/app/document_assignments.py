"""A07 段落 / 章节负责人（硬限制，17 §4.5）：归属计算、提交检查点、被拒保存。

- 只存显式分配（document_object_assignments）；归属在读取时计算：对象自身 → 最近的标题链（最近一层为准，不取并集）
  → 文档级 '@document' → 未分配。
- 检查点 ``enforce``：在 record_commit 里、_sync 写完对象之后、同一写事务内调用；比较 live（提交前）→ 新结果，
  只看 actor 本人的净改动。任何违规抛 409 SECTION_NOT_ASSIGNED，事务整体回滚（含 documents 的 CAS 写入）。
- 豁免：系统管理员、项目负责人、actor 为空（系统写入）、文档 assignment_mode='open'。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from app.errors import AppError
from app.markdown_blocks import heading_text, plain_text

DOCUMENT = "@document"
MAX_VIOLATIONS = 50
REJECTED_KEEP = 5
REJECTED_DAYS = 30
MESSAGE = "有 {n} 处修改在不由你负责的内容中，本次未保存。你的内容已保留。"


@dataclass
class Node:
    id: str
    kind: str
    level: int | None
    body: str


@dataclass
class Owner:
    owners: frozenset[str]
    via: str            # self / heading / document / none
    via_id: str | None


# ---------------------------------------------------------------- 读取

def explicit_map(connection, document_id) -> dict[str, frozenset[str]]:
    rows = connection.execute(
        "SELECT object_id, assignee_id FROM document_object_assignments WHERE document_id=%s", (str(document_id),),
    ).fetchall()
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(str(row["object_id"]), set()).add(str(row["assignee_id"]))
    return {key: frozenset(value) for key, value in result.items()}


def nodes_of(connection, document_id) -> list[Node]:
    cursor = connection._conn.cursor()
    cursor.row_factory = None
    return [Node(str(values[0]), values[1], values[2], values[3]) for values in cursor.execute(
        "SELECT id, kind, heading_level, body FROM document_objects WHERE document_id=? AND deleted_at IS NULL "
        "ORDER BY position, created_at", (str(document_id),)).fetchall()]


def resolve(nodes: list[Node], explicit: dict[str, frozenset[str]]) -> list[Owner]:
    """每个对象的生效负责人（17 §4.5.1）。标题自己有分配时 via='heading' 且 viaObjectId 为自身。"""
    document = explicit.get(DOCUMENT)
    fallback = Owner(document, "document", None) if document else Owner(frozenset(), "none", None)
    stack: list[tuple[int, Owner]] = []
    result: list[Owner] = []
    for node in nodes:
        if node.kind == "heading":
            level = int(node.level or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if node.id in explicit:
                owner = Owner(explicit[node.id], "heading", node.id)
            else:
                owner = stack[-1][1] if stack else fallback
            stack.append((level, owner))
            result.append(owner)
            continue
        if node.id in explicit:
            result.append(Owner(explicit[node.id], "self", node.id))
        else:
            result.append(stack[-1][1] if stack else fallback)
    return result


def heading_paths(nodes: list[Node]) -> list[list[str]]:
    stack: list[tuple[int, str]] = []
    paths = []
    for node in nodes:
        if node.kind == "heading":
            level = int(node.level or 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading_text(node.body)))
        paths.append([text for _level, text in stack])
    return paths


def actor_scope(connection, document_id, actor) -> tuple[str | None, bool, str]:
    """(项目角色, 是否系统管理员, assignment_mode)。"""
    row = connection.execute(
        """SELECT d.assignment_mode, m.role FROM documents d
           LEFT JOIN project_members m ON m.project_id = d.project_id AND m.user_id = %s WHERE d.id = %s""",
        (str(actor) if actor else None, str(document_id)),
    ).fetchone()
    admin = actor is not None and connection.execute(
        "SELECT 1 FROM user_roles WHERE user_id=%s AND role='system_admin'", (str(actor),)).fetchone() is not None
    return (None if row is None else row["role"]), admin, ("open" if row is None else row["assignment_mode"])


def restricted(role: str | None, admin: bool, mode: str) -> bool:
    """是否受硬限制：只有 enforced 文档上的编写人员（editor）。"""
    return not admin and role == "editor" and mode == "enforced"


def block_editable(role: str | None, admin: bool, mode: str, archived: bool, actor: str, owner: Owner) -> tuple[bool, str | None]:
    if archived:
        return False, "archived"
    if admin or role == "owner":
        return True, None
    if role != "editor":
        return False, "role"
    if mode == "open" or actor in owner.owners:
        return True, None
    return False, "assignment"


# ---------------------------------------------------------------- 检查点

def _moved(before_order: list[str], after_order: list[str]) -> set[str]:
    """相对顺序变化的 ID（最长递增子序列以外的为“移动”）。"""
    rank = {value: index for index, value in enumerate(before_order)}
    seq = [value for value in after_order if value in rank]
    values = [rank[value] for value in seq]
    tails: list[int] = []
    tail_index: list[int] = []
    prev = [-1] * len(values)
    for index, value in enumerate(values):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < value:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(value)
            tail_index.append(index)
        else:
            tails[lo] = value
            tail_index[lo] = index
        prev[index] = tail_index[lo - 1] if lo > 0 else -1
    stay: set[str] = set()
    index = tail_index[-1] if tail_index else -1
    while index >= 0:
        stay.add(seq[index])
        index = prev[index]
    return {value for value in seq if value not in stay}


def plan_inheritance(before_ids: set[str], after: list[Node], explicit: dict[str, frozenset[str]],
                     actor: str | None) -> dict[str, tuple[frozenset[str], str, bool]]:
    """相邻继承（17 §10.5）：连续新增的非标题块紧跟仍存在的块 P（中间无标题）时继承 P 的段落级分配；
    在章节开头（前面没有非标题块）时看紧跟其后的已有块 Q，只有 actor 是 Q 的分配人才继承。
    返回 {新对象 ID: (分配人集合, 源对象 ID, needs_confirm)}。"""
    result: dict[str, tuple[frozenset[str], str, bool]] = {}
    index = 0
    while index < len(after):
        node = after[index]
        if node.id in before_ids or node.kind == "heading":
            index += 1
            continue
        start = index
        while index < len(after) and after[index].id not in before_ids and after[index].kind != "heading":
            index += 1
        run = after[start:index]
        source = None
        previous = after[start - 1] if start > 0 else None
        if previous is not None and previous.kind != "heading" and previous.id in before_ids:
            if previous.id in explicit:
                source = previous.id
        elif index < len(after) and after[index].kind != "heading" and after[index].id in before_ids:
            following = after[index].id
            if following in explicit and actor in explicit[following]:
                source = following
        if source is not None:
            owners = explicit[source]
            for item in run:
                result[item.id] = (owners, source, actor not in owners)
    return result


def _violation(kind: str, node: Node, index: int | None, owner: Owner, path: list[str], names: dict[str, str]) -> dict:
    return {"type": kind, "objectId": node.id, "clientIndex": index,
            "owners": [{"id": value, "name": names.get(value, "")} for value in sorted(owner.owners)],
            "headingPath": path, "excerpt": (heading_text(node.body) if node.kind == "heading" else plain_text(node.body))[:40]}


def _names(connection, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    marks = ",".join(["%s"] * len(ids))
    return {str(row["id"]): row["name"] for row in connection.execute(
        f"SELECT id, name FROM users WHERE id IN ({marks})", tuple(ids)).fetchall()}


def check(before: list[Node], after: list[Node], explicit: dict[str, frozenset[str]], actor: str,
          inherited: dict[str, tuple[frozenset[str], str, bool]] | None = None) -> list[tuple[str, Node, int | None, Owner, bool]]:
    """规则 1–6（17 §4.5.2）。返回 [(type, node, clientIndex, owner, from_before)]。纯函数，便于单元测试与 fuzz。"""
    inherited = plan_inheritance({n.id for n in before}, after, explicit, actor) if inherited is None else inherited
    planned = dict(explicit)
    for object_id, (owners, _source, _confirm) in inherited.items():
        planned[object_id] = owners
    gone = {n.id for n in before} - {n.id for n in after}
    for object_id in gone:
        planned.pop(object_id, None)
    owners_before = dict(zip((n.id for n in before), resolve(before, explicit)))
    owners_after = dict(zip((n.id for n in after), resolve(after, planned)))
    before_by = {n.id: n for n in before}
    moved = _moved([n.id for n in before], [n.id for n in after])
    violations: list[tuple[str, Node, int | None, Owner, bool]] = []
    for node in before:
        if node.id in gone:
            if node.kind == "heading" and node.id in explicit:
                violations.append(("heading_structure", node, None, owners_before[node.id], True))
            elif actor not in owners_before[node.id].owners:
                violations.append(("delete", node, None, owners_before[node.id], True))
    for index, node in enumerate(after):
        old = before_by.get(node.id)
        after_owner = owners_after[node.id]
        if old is None:
            if actor not in after_owner.owners:
                violations.append(("insert", node, index, after_owner, False))
            continue
        before_owner = owners_before[node.id]
        structural = old.kind != node.kind or (old.level or 0) != (node.level or 0)
        if old.kind == "heading" and old.id in explicit and (structural or node.id in moved):
            violations.append(("heading_structure", node, index, before_owner, False))
            continue
        if node.id in moved:
            if actor not in before_owner.owners:
                violations.append(("move", node, index, before_owner, False))
            elif actor not in after_owner.owners:
                violations.append(("move", node, index, after_owner, False))
            continue
        if old.body != node.body or structural:
            if actor not in before_owner.owners:
                violations.append(("modify", node, index, before_owner, False))
            elif actor not in after_owner.owners:
                violations.append(("reassign", node, index, after_owner, False))
            continue
        if before_owner.owners != after_owner.owners:
            violations.append(("reassign", node, index, before_owner, False))
    return violations


def enforce(connection, document_id, actor, before: list[Node], doc_revision: int) -> None:
    """record_commit 在 _sync 之后调用（同一写事务）。通过时落实相邻继承、删除已消失对象的分配。"""
    explicit = explicit_map(connection, document_id)
    after = nodes_of(connection, document_id)
    actor_id = None if actor is None else str(actor)
    inherited = plan_inheritance({n.id for n in before}, after, explicit, actor_id)
    role, admin, mode = (None, False, "open") if actor is None else actor_scope(connection, document_id, actor)
    if actor is not None and restricted(role, admin, mode):
        found = check(before, after, explicit, actor_id, inherited)
        if found:
            ids = {value for item in found for value in item[3].owners}
            names = _names(connection, ids)
            paths_before, paths_after = heading_paths(before), heading_paths(after)
            index_before = {n.id: i for i, n in enumerate(before)}
            items = []
            for kind, node, index, owner, from_before in found[:MAX_VIOLATIONS]:
                path = paths_before[index_before[node.id]] if from_before else paths_after[index]
                items.append(_violation(kind, node, index, owner, path, names))
            raise AppError(409, "SECTION_NOT_ASSIGNED", MESSAGE.format(n=len(found)), details={
                "violations": items, "total": len(found),
                "assignmentsRevision": assignments_revision(connection, document_id)})
    # 通过：拆分 / 相邻继承写入段落级分配；消失对象的显式分配删除（分配随对象结束）
    changed = False
    for object_id, (owners, source, confirm) in inherited.items():
        for assignee in owners:
            connection.execute(
                """INSERT OR IGNORE INTO document_object_assignments
                       (document_id, object_id, assignee_id, source, needs_confirm, assigned_by, assigned_at)
                   VALUES (%s,%s,%s,'split',%s,%s,now())""",
                (str(document_id), object_id, assignee, int(confirm), actor_id))
        changed = True
    alive = {m.id for m in after}
    gone = [n.id for n in before if n.id not in alive and n.id in explicit]
    for object_id in gone:
        connection.execute("DELETE FROM document_object_assignments WHERE document_id=%s AND object_id=%s",
                           (str(document_id), object_id))
        changed = True
    if changed:
        bump(connection, document_id)
        if gone:
            event(connection, document_id, "assignment", "lost", gone, None, actor_id, doc_revision)


def ai_precheck(connection, document_id, actor, heading_index: int | None, whole_document: bool,
                heading_text: str | None = None) -> None:
    """17 §4.5.4 / 27 M-3：受限编写人员在调模型之前检查范围，越权直接 409，不消耗 AI 额度。
    全文编写不对受限编写人员开放；章节编写要求该章标题归自己（章内例外仍由写入检查点把关）。"""
    if actor is None or not restricted(*actor_scope(connection, document_id, actor)):
        return
    revision = assignments_revision(connection, document_id)
    if whole_document:
        raise AppError(409, "SECTION_NOT_ASSIGNED", "全文编写只有项目负责人可以使用。",
                       details={"violations": [], "total": 0, "assignmentsRevision": revision, "reason": "document_scope"})
    if heading_index is None:
        return
    nodes = nodes_of(connection, document_id)
    owners = resolve(nodes, explicit_map(connection, document_id))
    headings = [(node, owner, path) for node, owner, path in zip(nodes, owners, heading_paths(nodes)) if node.kind == "heading"]
    picked = headings[heading_index] if heading_index < len(headings) else None
    wanted = None if heading_text is None else _plain_heading("# " + heading_text)
    if wanted is not None and (picked is None or _plain_heading(picked[0].body) != wanted):
        # 两种取法对不上：按写作取到的标题文字找对象（同名取离序号最近的）；找不到就按受限处理，不放行
        same = [(abs(i - heading_index), item) for i, item in enumerate(headings) if _plain_heading(item[0].body) == wanted]
        picked = min(same, key=lambda pair: pair[0])[1] if same else None
        if picked is None:
            raise AppError(409, "SECTION_NOT_ASSIGNED", "所选范围包含他人负责的内容，请只选择你负责的部分。",
                           details={"violations": [], "total": 0, "assignmentsRevision": revision, "reason": "ai_scope"})
    if picked is None:
        return
    node, owner, path = picked
    if str(actor) in owner.owners:
        return
    names = _names(connection, set(owner.owners))
    raise AppError(409, "SECTION_NOT_ASSIGNED", "所选范围包含他人负责的内容，请只选择你负责的部分。",
                   details={"violations": [_violation("modify", node, None, owner, path, names)], "total": 1,
                            "assignmentsRevision": revision, "reason": "ai_scope"})


def _plain_heading(body: str) -> str:
    import re
    return re.sub(r"[ \t]+#+[ \t]*$", "", re.sub(r"^ {0,3}#{1,6}[ \t]*", "", body.strip())).strip()


def _plain_text(text: str) -> str:
    import re
    return re.sub(r"[\s*_`~#>|\\\[\]()!-]+", "", text)


def selection_precheck(connection, document_id, actor, probe_markdown: str, block_ids: list[str] | None,
                       selection_text: str = "") -> None:
    """27 复验 M-3 / 28 M-1''：受限编写人员的选区改写按选区块 ID 定位对象（不按文字找，重复文字不能绕过）：
    必须带 selectionBlockIds（缺 → 422），ID 都得是当前正文里的对象，选中的文字得落在这些块里，且这些块都归本人；
    正文里被替换的块（探针）也要归本人。"""
    if actor is None or not restricted(*actor_scope(connection, document_id, actor)):
        return
    if not block_ids:
        raise AppError(422, "VALIDATION_FAILED", "请重新选择正文并生成（缺少所选段落的标识）。",
                       field_errors=[{"field": "selectionBlockIds", "message": "受限编写人员的选区编写必须带所选块的标识。"}])
    from app.markdown_blocks import split
    nodes = nodes_of(connection, document_id)
    by_id = {node.id: index for index, node in enumerate(nodes)}
    if any(value not in by_id for value in block_ids):
        raise AppError(422, "SELECTION_INVALID", "所选段落已变化，请重新选择正文并生成。")
    picked = sorted({by_id[value] for value in block_ids})
    wanted_text = _plain_text(selection_text)
    if wanted_text and wanted_text not in _plain_text("".join(nodes[index].body for index in picked)):
        raise AppError(422, "SELECTION_INVALID", "所选文字与所选段落对不上，请重新选择正文并生成。")
    probe_blocks = split(probe_markdown)[1]
    probe = {block.body.strip() for block in probe_blocks}
    owners = resolve(nodes, explicit_map(connection, document_id))
    paths = heading_paths(nodes)
    for index, (node, owner) in enumerate(zip(nodes, owners)):
        # 被替换的块按位置找（块数不变时逐块比对，重复文字也能分清）；块数变了退回按文字找
        changed = (probe_blocks[index].body.strip() != node.body.strip()) if len(probe_blocks) == len(nodes) else node.body.strip() not in probe
        touched = index in picked or changed
        if not touched or str(actor) in owner.owners:
            continue
        names = _names(connection, set(owner.owners))
        raise AppError(409, "SECTION_NOT_ASSIGNED", "所选范围包含他人负责的内容，请只选择你负责的部分。",
                       details={"violations": [_violation("modify", node, None, owner, paths[index], names)], "total": 1,
                                "assignmentsRevision": assignments_revision(connection, document_id), "reason": "ai_scope"})


def identities_drop_assignments(connection, document_id, before: list[Node], blocks, identities) -> bool:
    """块标识会让有显式分配的对象消失，而它的正文原样出现在新正文里（说明是配错了 id，不是真删除）。"""
    explicit = explicit_map(connection, document_id)
    kept = {value for value in identities if value}
    bodies = {block.body.strip() for block in blocks}
    return any(node.id in explicit and node.id not in kept and node.body.strip() in bodies for node in before)


def assignments_revision(connection, document_id) -> int:
    row = connection.execute("SELECT assignments_revision FROM documents WHERE id=%s", (str(document_id),)).fetchone()
    return 0 if row is None else int(row["assignments_revision"])


def bump(connection, document_id) -> int:
    connection.execute("UPDATE documents SET assignments_revision = assignments_revision + 1 WHERE id=%s", (str(document_id),))
    return assignments_revision(connection, document_id)


def event(connection, document_id, kind: str, action: str, object_ids: list[str], subject, actor, doc_revision=None,
          payload: dict | None = None) -> None:
    connection.execute(
        """INSERT INTO document_annotation_events (id, document_id, kind, action, object_ids, subject_user_id, payload,
               actor_id, doc_revision, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())""",
        (str(uuid4()), str(document_id), kind, action, json.dumps(object_ids), None if subject is None else str(subject),
         json.dumps(payload or {}, ensure_ascii=False), None if actor is None else str(actor), doc_revision))


# ---------------------------------------------------------------- 被拒保存

def store_rejected(connection, document_id, actor, base_revision: int, markdown: str, block_ids, error: AppError) -> str:
    """在独立短事务里保存被拒提交（主事务已回滚）；把 rejectedSaveId 放进 error.details。"""
    from app.db import write_transaction

    if connection.in_transaction:
        connection.rollback()
    rid = str(uuid4())
    violations = (error.details or {}).get("violations", [])
    with write_transaction(connection):
        connection.execute(
            """INSERT INTO document_rejected_saves (id, document_id, actor_id, base_revision, markdown, block_ids,
                   violations, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),now())""",
            (rid, str(document_id), str(actor), base_revision, markdown,
             None if block_ids is None else json.dumps(block_ids), json.dumps(violations, ensure_ascii=False)))
        event(connection, document_id, "assignment_violation", "rejected", [v["objectId"] for v in violations if v.get("objectId")],
              actor, actor, base_revision, {"rejectedSaveId": rid, "total": (error.details or {}).get("total")})
        prune_rejected(connection, document_id, actor)
    details = dict(error.details or {})
    details["rejectedSaveId"] = rid
    error.details = details
    return rid


def prune_rejected(connection, document_id=None, actor=None) -> int:
    """每人每文档保留最近 5 份、30 天（最新一份只受 30 天限制）。document_id=None 时全库清理（worker 定时）。"""
    removed = connection.execute(
        "DELETE FROM document_rejected_saves WHERE created_at < datetime('now', %s)", (f"-{REJECTED_DAYS} days",)).rowcount
    where, args = ("", ()) if document_id is None else (" WHERE document_id=%s AND actor_id=%s", (str(document_id), str(actor)))
    groups = connection.execute(f"SELECT DISTINCT document_id, actor_id FROM document_rejected_saves{where}", args).fetchall()
    for group in groups:
        rows = connection.execute(
            "SELECT id FROM document_rejected_saves WHERE document_id=%s AND actor_id=%s ORDER BY created_at DESC, rowid DESC",
            (group["document_id"], group["actor_id"])).fetchall()
        for row in rows[REJECTED_KEEP:]:
            connection.execute("DELETE FROM document_rejected_saves WHERE id=%s", (row["id"],))
            removed += 1
    return removed


def sanitize_identities(live_ids: dict[str, str], kinds: list[str], identities: list[str | None]) -> list[str | None]:
    """不可信的 blockIds：不属于本文档、已删除、类别不符、重复出现的一律当 null（17 §4.1）。"""
    seen: set[str] = set()
    result: list[str | None] = []
    for value, kind in zip(identities, kinds):
        if value is None or value in seen or live_ids.get(value) is None or live_ids[value] != kind:
            result.append(None)
            continue
        seen.add(value)
        result.append(value)
    return result


