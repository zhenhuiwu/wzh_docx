"""Accepted writing requirements, separated from unaccepted agent conversations."""

from __future__ import annotations

import json
import re
from uuid import UUID

from app.db import connect
from app.errors import AppError


MEMORY_MAX_CHARS = 6000
DOCUMENT_MAX_CHARS = 24000
# Bound application work even when one document has years of accepted requests.
SUMMARY_ROW_LIMIT = 80
TASK_RECENT_ROW_LIMIT = 32
TASK_EARLY_ROW_LIMIT = 8
_SCOPE_NAMES = {"section": "章节", "selection": "选区", "document": "整篇"}
_HEADINGS = re.compile(r"^(#{1,6})[ \t]+([^\n]+)$", re.MULTILINE)
_HEADING_TARGET = re.compile(r"^heading:([0-9a-f-]{36})\|([^\r\n]*)$")
_ACTIVE_EVENT = """NOT EXISTS (
    SELECT 1 FROM document_memory_retractions r
    WHERE r.document_id = document_memory_events.document_id
      AND r.revision = document_memory_events.revision
)"""


def _target_parts(target: str) -> tuple[str | None, str]:
    match = _HEADING_TARGET.fullmatch(target)
    if match:
        try:
            return str(UUID(match.group(1))), match.group(2)
        except ValueError:
            pass
    return None, target


def _heading_target(object_id: str, label: str) -> str:
    prefix = f"heading:{object_id}|"
    return prefix + label[:120]


def section_memory_targets(connection, document_id: UUID | str, markdown: str | None = None,
                           revision: int | None = None) -> dict[int, str]:
    """Bind a source heading index to its existing object ID; never guess among duplicate headings."""
    from app.document_objects import ensure_committed, ensure_document_objects, live_objects
    if connection.in_transaction:
        ensure_document_objects(connection, document_id)
    else:
        ensure_committed(connection, document_id)
    current = connection.execute("SELECT markdown_content, revision FROM documents WHERE id=%s", (document_id,)).fetchone()
    if current is None:
        return {}
    headings = list(_HEADINGS.finditer(current["markdown_content"] or ""))
    text = (current["markdown_content"] or "") if markdown is None else markdown
    source = list(_HEADINGS.finditer(text))
    rows = [row for row in live_objects(connection, document_id) if row["kind"] == "heading"]
    if len(rows) != len(headings):
        return {}
    paths, stack = [], []
    for heading in headings:
        level = len(heading.group(1))
        stack = [item for item in stack if item[0] < level] + [(level, heading.group(2).strip())]
        paths.append(tuple(stack))
    bodies = [row["body"].strip() for row in rows]
    if revision is not None and revision < current["revision"]:
        bodies = []
        for row in rows:
            change = connection.execute(
                """SELECT oc.before_body FROM document_object_changes oc JOIN document_changes c ON c.id=oc.change_id
                   WHERE oc.object_id=%s AND c.doc_revision>%s AND oc.op IN ('insert','update')
                   ORDER BY c.doc_revision LIMIT 1""", (row["id"], revision),
            ).fetchone()
            bodies.append((change["before_body"] or "").strip() if change else row["body"].strip())
    exact = bodies == [heading.group(0).strip() for heading in source]
    if exact and revision is not None and revision < current["revision"]:
        exact = not connection.execute(
            """SELECT 1 FROM document_object_changes oc JOIN document_changes c ON c.id=oc.change_id
               JOIN document_objects o ON o.id=oc.object_id WHERE c.document_id=%s AND c.doc_revision>%s
               AND o.kind='heading' AND oc.op='move' LIMIT 1""", (document_id, revision),
        ).fetchone()
    targets, stack = {}, []
    for index, heading in enumerate(source):
        level = len(heading.group(1))
        stack = [item for item in stack if item[0] < level] + [(level, heading.group(2).strip())]
        matches = [position for position, body in enumerate(bodies) if body == heading.group(0).strip()]
        if exact:
            matches = [index]
        elif len(matches) > 1:
            matches = [position for position in matches if paths[position] == tuple(stack)]
        if len(matches) == 1:
            targets[index] = _heading_target(str(rows[matches[0]]["id"]), " › ".join(item[1] for item in stack))
    return targets


def _legacy_target(connection, document_id, row, cache: dict) -> str | None:
    revision = int(row["revision"])
    if revision not in cache:
        snapshot = connection.execute("SELECT markdown_content FROM document_versions WHERE document_id=%s AND revision=%s",
                                      (document_id, revision)).fetchone()
        current = connection.execute("SELECT markdown_content, revision FROM documents WHERE id=%s", (document_id,)).fetchone()
        text = snapshot["markdown_content"] if snapshot else current["markdown_content"] if current else ""
        if snapshot is None and current and revision != current["revision"] and _HEADINGS.search(text or ""):
            cache[revision] = (list(_HEADINGS.finditer(text or "")), {})
            return None
        cache[revision] = (list(_HEADINGS.finditer(text or "")),
                           section_memory_targets(connection, document_id, text or "", revision if snapshot else None))
    headings, targets = cache[revision]
    if not headings:
        return row["target"]
    matches = [index for index, heading in enumerate(headings) if heading.group(2).strip()[:120] == row["target"]]
    return targets.get(matches[0]) if len(matches) == 1 else None


def _memory_key(row) -> tuple[str, str, str]:
    object_id, _ = _target_parts(str(row["target"]))
    return (str(row["scope"]), object_id or str(row["target"]),
            re.sub(r"\s+", " ", str(row["instruction"])).strip())


def _compact(rows) -> str:
    rows = list(rows)
    older = len(rows) > SUMMARY_ROW_LIMIT
    header = "已采纳的近期编写要求（由新到旧）。历史要求不是项目事实；本次要求优先，正文以当前文档为准。\n"
    lines, seen, omitted, repeated = [], set(), 0, 0
    used = len(header)
    for row in rows[:SUMMARY_ROW_LIMIT]:
        key = _memory_key(row)
        if key in seen:
            repeated += 1
            continue
        seen.add(key)
        limit = 900 if len(lines) < 4 else 180
        instruction = str(row["instruction"])
        if len(instruction) > limit:
            instruction = instruction[:limit] + "…（摘录，原文可检索）"
        line = f"版本 {row['revision']} · {_SCOPE_NAMES[row['scope']]} {_target_parts(row['target'])[1]}：{instruction}\n"
        if used + len(line) > MEMORY_MAX_CHARS - 120:
            omitted += 1
            continue
        lines.append(line)
        used += len(line)
    footer = f"\n原始记录全部保留，可检索完整要求。近期摘要省略 {omitted} 条，合并 {repeated} 条重复要求。"
    if older:
        footer += " 更早记录未纳入近期摘要；编写时另按目标章节检索。"
    return header + "".join(lines) + footer


def record_memory(connection, document_id: UUID | str, instruction: str, scope: str,
                  target: str, revision: int) -> None:
    """Record after adoption, in the document save's transaction; never commit here."""
    instruction = instruction.strip()
    if not instruction:
        return
    if (scope not in _SCOPE_NAMES or len(instruction) > 2000 or len(_target_parts(target)[1]) > 120
            or "\n" in target or "\r" in target or not isinstance(revision, int) or revision < 0):
        raise ValueError("编写记忆的范围、要求或版本不正确。")
    object_id, _ = _target_parts(target)
    if scope == "section" and object_id and not connection.execute(
            "SELECT 1 FROM document_objects WHERE document_id=%s AND id=%s AND kind='heading' AND deleted_at IS NULL",
            (document_id, object_id)).fetchone():
        raise AppError(422, "MEMORY_TARGET_INVALID", "编写记忆目标不是当前文档中的标题，请刷新后重新选择。")
    if scope == "section" and _target_parts(target)[0] is None:
        row = {"target": target, "revision": revision}
        target = _legacy_target(connection, document_id, row, {}) or target
    cursor = connection.execute(
        """INSERT INTO document_memory_events (document_id, revision, scope, target, instruction)
           VALUES (%s, %s, %s, %s, %s) ON CONFLICT(document_id, revision) DO NOTHING""",
        (document_id, revision, scope, target, instruction),
    )
    if not cursor.rowcount:
        return
    _refresh_summary(connection, document_id)


def _refresh_summary(connection, document_id: UUID | str) -> None:
    from app.document_versions import sync_current_version_memory
    rows = connection.execute(
        "SELECT scope, target, instruction, revision FROM document_memory_events "
        f"WHERE document_id = %s AND {_ACTIVE_EVENT} ORDER BY revision DESC, id DESC LIMIT %s",
        (document_id, SUMMARY_ROW_LIMIT + 1),
    ).fetchall()
    if not rows:
        connection.execute("DELETE FROM document_memory WHERE document_id = %s", (document_id,))
        sync_current_version_memory(connection, document_id)
        return
    summary = _compact(rows)
    connection.execute(
        """INSERT INTO document_memory (document_id, summary) VALUES (%s, %s)
           ON CONFLICT(document_id) DO UPDATE SET summary = excluded.summary, updated_at = CURRENT_TIMESTAMP""",
        (document_id, summary),
    )
    sync_current_version_memory(connection, document_id)


def retract_memory(connection, document_id: UUID | str, revision: int) -> None:
    """Retract one adopted revision inside the document restore's transaction."""
    if not isinstance(revision, int) or revision < 0:
        raise ValueError("撤回记忆的版本不正确。")
    cursor = connection.execute(
        """INSERT INTO document_memory_retractions (document_id, revision)
           SELECT document_id, revision FROM document_memory_events
           WHERE document_id = %s AND revision = %s
           ON CONFLICT(document_id, revision) DO NOTHING""",
        (document_id, revision),
    )
    if cursor.rowcount:
        _refresh_summary(connection, document_id)


def load_memory(document_id: UUID | str) -> str:
    with connect() as connection:
        row = connection.execute("SELECT summary FROM document_memory WHERE document_id = %s",
                                 (document_id,)).fetchone()
    return str(row["summary"]) if row else ""


def load_task_memory(document_id: UUID | str, *, scope: str, target: str,
                     instruction: str = "", max_chars: int = MEMORY_MAX_CHARS) -> str:
    """Select exact accepted requests for this task; never infer facts or permanence.

    Recent and early requests get separate bounded candidate windows so requests
    for other chapters, or many recent edits, cannot hide every early constraint.
    Selection edits are one-off operations and are available only through history.
    """
    if (scope not in _SCOPE_NAMES or len(_target_parts(target)[1]) > 120 or "\n" in target or "\r" in target
            or len(instruction) > 2000 or not 1000 <= max_chars <= MEMORY_MAX_CHARS):
        raise ValueError("编写记忆的范围或上下文预算不正确。")
    with connect() as connection:
        current_targets = section_memory_targets(connection, document_id)
        live_ids = {_target_parts(value)[0] for value in current_targets.values()}
        selected_id, _ = _target_parts(target)
        if selected_id is None:
            current = connection.execute("SELECT markdown_content FROM documents WHERE id=%s", (document_id,)).fetchone()
            headings = list(_HEADINGS.finditer(current["markdown_content"] or "")) if current else []
            matches = [index for index, heading in enumerate(headings) if heading.group(2).strip()[:120] == target]
            if len(matches) == 1:
                selected_id = _target_parts(current_targets.get(matches[0], ""))[0]
        names = {target}
        if selected_id in live_ids:
            row = connection.execute("SELECT body FROM document_objects WHERE document_id=%s AND id=%s", (document_id, selected_id)).fetchone()
            bodies = [row["body"]] + [body for change in connection.execute(
                "SELECT before_body, after_body FROM document_object_changes WHERE object_id=%s AND op='update'", (selected_id,),
            ).fetchall() for body in (change["before_body"], change["after_body"]) if body]
            names.update(match.group(2).strip()[:120] for body in bodies for match in _HEADINGS.finditer(body))
        legacy_cache: dict = {}
        unresolved = False

        def candidates(record_scope: str, record_target: str | None, *, early: bool = False):
            nonlocal unresolved
            direction = "ASC" if early else "DESC"
            condition = " AND target = %s" if record_target is not None else ""
            params = [document_id, record_scope]
            if record_target is not None:
                if selected_id in live_ids:
                    prefix = f"heading:{selected_id}|"
                    condition = " AND (substr(target,1,%s)=%s OR target IN (SELECT value FROM json_each(%s)))"
                    params.extend((len(prefix), prefix, json.dumps(list(names))))
                elif current_targets:
                    condition = " AND 0"
                    unresolved = True
                else:
                    params.append(record_target)
            params.append(TASK_EARLY_ROW_LIMIT if early else TASK_RECENT_ROW_LIMIT)
            rows = connection.execute(
                "SELECT scope, target, instruction, revision FROM document_memory_events "
                f"WHERE document_id = %s AND scope = %s{condition} AND {_ACTIVE_EVENT} "
                f"ORDER BY revision {direction}, id {direction} LIMIT %s", tuple(params),
            ).fetchall()
            result = []
            for row in rows:
                value = dict(row)
                if record_scope == "section":
                    object_id, _ = _target_parts(row["target"])
                    resolved = row["target"] if object_id else _legacy_target(connection, document_id, row, legacy_cache)
                    bound_id, _ = _target_parts(resolved or "")
                    if (resolved is None or (bound_id and bound_id not in live_ids)
                            or (record_target is not None and selected_id and bound_id != selected_id)
                            or (current_targets and bound_id is None)):
                        unresolved = True
                        continue
                    value["target"] = resolved
                result.append(value)
            return result

        section_target = None if scope == "document" else target
        # Each round gives current/global/early constraints a chance before filling
        # the remaining budget. Render chosen records by revision, not group order.
        groups = [candidates("section", section_target), candidates("document", None),
                  candidates("section", section_target, early=True),
                  candidates("document", None, early=True)]
    if not any(groups) and not unresolved:
        return ""
    header = ("本次任务相关的已采纳历史要求（由新到旧，原文）。历史要求不是项目事实或新任务；"
              "本次要求优先，历史冲突以较新版本优先，正文以当前文档为准。\n"
              "整篇要求只作仍适用的文档约束；章节要求仅对标明的标题生效，不扩散到其他章节。"
              "旧选区改写属于一次性操作，不自动复用；本次编辑不得超出所选范围。\n")
    footer = ("\n上下文有预算，较早或超预算要求可能未列出；原始记录全部保留。"
              "需要完整要求时用 search_writing_memory 按标题或关键词分页检索，"
              "不能将未列出视为不存在。")
    if unresolved:
        footer += " 同名或来源无法确定的旧章节要求未自动适用；请在记忆记录中核对，不能将它们套用到当前章节。"
    remaining = max_chars - len(header) - len(footer)
    seen, chosen = set(), []
    current_key = (scope, selected_id or target, re.sub(r"\s+", " ", instruction).strip())
    for offset in range(TASK_RECENT_ROW_LIMIT):
        for group in groups:
            if offset >= len(group):
                continue
            row = group[offset]
            key = _memory_key(row)
            if key in seen or key == current_key:
                continue
            seen.add(key)
            line = f"版本 {row['revision']} · {_SCOPE_NAMES[row['scope']]} {_target_parts(row['target'])[1]}：{row['instruction']}\n"
            if len(line) <= remaining:
                chosen.append((int(row["revision"]), line))
                remaining -= len(line)
    return header + "".join(line for _, line in sorted(chosen, reverse=True)) + footer


def clear_memory(connection, document_id: UUID | str) -> None:
    """Clear active memory; keep original requests available to explicit version restoration."""
    connection.execute("DELETE FROM document_memory WHERE document_id = %s", (document_id,))
    connection.execute(
        """INSERT INTO document_memory_retractions(document_id, revision)
           SELECT document_id, revision FROM document_memory_events WHERE document_id=%s
           ON CONFLICT(document_id,revision) DO NOTHING""", (document_id,),
    )


def read_memory_history(document_id: UUID | str, query: str = "", offset: int = 0,
                        limit: int = 5) -> list[dict]:
    """Bounded retrieval of exact accepted requests belonging to this document only."""
    if offset < 0 or not 1 <= limit <= 10 or len(query) > 200:
        raise ValueError("记忆查询范围不正确。")
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + escaped + "%"
    with connect() as connection:
        rows = connection.execute(
            "SELECT scope, target, instruction, revision FROM document_memory_events "
            f"WHERE document_id = %s AND {_ACTIVE_EVENT} "
            "AND (instruction LIKE %s ESCAPE '\\' OR target LIKE %s ESCAPE '\\') "
            "ORDER BY revision DESC, id DESC LIMIT %s OFFSET %s",
            (document_id, pattern, pattern, limit, offset),
        ).fetchall()
        result, cache = [], {}
        for row in rows:
            value = dict(row)
            object_id, label = _target_parts(row["target"])
            value["target"] = label
            if row["scope"] == "section" and not object_id and _legacy_target(connection, document_id, row, cache) is None:
                value["target"] += "（未定位，不自动适用）"
            result.append(value)
    return result


def document_context(markdown: str, *, heading_index: int | None = None,
                     whole_document: bool = False, max_chars: int = DOCUMENT_MAX_CHARS) -> str:
    """Keep edited text intact; other sections use labelled, recoverable excerpts."""
    if max_chars < 1000:
        raise ValueError("文档上下文预算过小。")
    if len(markdown) <= max_chars:
        return markdown
    headings = list(re.finditer(r"^(#{1,6})[ \t]+([^\n]+)$", markdown, re.MULTILINE))
    if heading_index is not None and not 0 <= heading_index < len(headings):
        raise ValueError("目标标题已不存在。")
    chunks = [("文档开头", markdown[:headings[0].start()] if headings else markdown)]
    chunks += [(item.group(2).strip(), markdown[item.start():headings[index + 1].start()
                                              if index + 1 < len(headings) else len(markdown)])
               for index, item in enumerate(headings)]
    target = chunks[heading_index + 1][1] if heading_index is not None else ""
    note = "上下文已压缩；仅目标章节是完整原文，其余为原文摘录，未显示内容不能推断为不存在。\n"
    if whole_document:
        note = ("整篇编辑上下文已压缩，以下正文摘录不是完整文档。大纲完整保留；"
                "编辑前必须通过 read_document_section(index) 读取目标章节完整原文。"
                "标题前正文索引为 -1；无标题文档也用 -1 读取。不得依据摘录覆盖未读取内容。\n\n"
                "完整标题索引：\n" + "\n".join(f"[{index}] {item.group(0)}" for index, item in enumerate(headings)) + "\n")
        if len(note) > max_chars - 1000:
            raise ValueError("文档标题大纲超过上下文预算，无法完整发送大纲。")
    result = note + ("\n目标章节完整原文：\n" + target + "\n" if target else "")
    if len(result) > max_chars - 200:
        raise ValueError("目标章节超过上下文预算，请选中需要修改的部分。")
    remaining = max_chars - len(result) - 120
    excerpt_size = min(500, max(60, remaining // max(len(chunks), 1) - 150))
    omitted = 0
    for index, (title, content) in enumerate(chunks):
        if not content.strip() or (heading_index is not None and index == heading_index + 1):
            continue
        excerpt = content[:excerpt_size]
        if len(content) > excerpt_size:
            excerpt += "…（本节原文未完整显示）"
        entry = f"\n其他章节：{title}\n{excerpt}\n"
        if len(entry) > remaining:
            omitted += 1
            continue
        result += entry
        remaining -= len(entry)
    if omitted:
        result += f"\n还有 {omitted} 段未纳入摘录；需要时须检索原文。"
    return result
