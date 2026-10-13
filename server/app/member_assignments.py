"""项目成员的显式文档分工统计与清理；清理和成员变更使用同一个事务。"""

from __future__ import annotations

from uuid import UUID

EMPTY: dict[str, int] = {"sections": 0, "blocks": 0, "documents": 0}

# 失去编辑权（只读/移出）时需要清理分工的角色变化：A07 只把 owner/editor 作为可分配对象（ASSIGNEE_NOT_ELIGIBLE）。
EDITING_ROLES = frozenset({"owner", "editor"})


def empty() -> dict[str, int]:
    return dict(EMPTY)


def assignment_counts(connection, project_id: UUID) -> dict[str, dict[str, int]]:
    """{userId: {sections, blocks, documents}}；没有分工的成员不出现在结果里（调用方补 0）。"""
    rows = connection.execute(
        "SELECT a.assignee_id, count(DISTINCT a.document_id) AS documents, "
        "sum(CASE WHEN o.kind='heading' THEN 1 ELSE 0 END) AS sections, "
        "sum(CASE WHEN o.kind<>'heading' THEN 1 ELSE 0 END) AS blocks "
        "FROM document_object_assignments a JOIN documents d ON d.id=a.document_id "
        "JOIN document_objects o ON o.id=a.object_id WHERE d.project_id=%s AND o.deleted_at IS NULL GROUP BY a.assignee_id",
        (project_id,)).fetchall()
    return {str(row["assignee_id"]): {key: int(row[key]) for key in EMPTY} for row in rows}


def clear_assignments(connection, project_id: UUID, user_id: UUID, actor_id: UUID | None) -> dict[str, int]:
    """删除 user_id 在本项目各文档中的显式分配，返回被清掉的数目。必须在调用方的写事务内执行。"""
    from app.document_assignments import record_assignment_event
    counts = assignment_counts(connection, project_id).get(str(user_id), empty())
    rows = connection.execute(
        "SELECT a.document_id,a.object_id FROM document_object_assignments a JOIN documents d ON d.id=a.document_id "
        "WHERE d.project_id=%s AND a.assignee_id=%s", (project_id, user_id)).fetchall()
    changes = {}
    for row in rows:
        changes.setdefault(str(row["document_id"]), []).append({"objectId": str(row["object_id"]), "before": str(user_id), "after": None})
    connection.execute(
        "DELETE FROM document_object_assignments WHERE assignee_id=%s AND document_id IN (SELECT id FROM documents WHERE project_id=%s)",
        (user_id, project_id))
    for did, items in changes.items():
        record_assignment_event(connection, did, actor_id, items)
    return counts


def loses_editing(old_role: str | None, new_role: str | None) -> bool:
    """old → new（new=None 表示移出）是否让成员失去编辑权，需要清理分工。"""
    return old_role in EDITING_ROLES and new_role not in EDITING_ROLES
