"""A05 ↔ A07 的接口：成员在本项目文档中的显式分工（段落负责人）。

A07（迁移 118，document_object_assignments）已实现这两个挂钩：
- ``assignment_counts``：成员列表每项的 ``assignments{sections, blocks, documents}``（17 §10.1 K-5，移除/降级前的确认数）；
- ``clear_assignments``：移出成员或降为只读成员时，在**同一个成员写事务**里删除他在本项目的分配并返回
  ``assignmentsCleared{sections, blocks, documents}``（17 §2.3 R4 联动、U-13）。

A07 落地时只改这两个函数的实现（按 assignee_id 分组统计、删除分配并写 assignment 事件、受影响文档
assignments_revision + 1），A05 的接口字段和调用位置都不用动。现在没有分工数据，两者都返回 0。
"""

from __future__ import annotations

from uuid import UUID

EMPTY: dict[str, int] = {"sections": 0, "blocks": 0, "documents": 0}

# 失去编辑权（只读/移出）时需要清理分工的角色变化：A07 只把 owner/editor 作为可分配对象（ASSIGNEE_NOT_ELIGIBLE）。
EDITING_ROLES = frozenset({"owner", "editor"})


def empty() -> dict[str, int]:
    return dict(EMPTY)


def _count(rows) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    documents: dict[str, set[str]] = {}
    for row in rows:
        key = str(row["assignee_id"])
        item = result.setdefault(key, empty())
        documents.setdefault(key, set()).add(str(row["document_id"]))
        if row["object_id"] == "@document" or row["kind"] == "heading":
            item["sections"] += 1
        else:
            item["blocks"] += 1
    for key, ids in documents.items():
        result[key]["documents"] = len(ids)
    return result


_ROWS = """select a.assignee_id, a.document_id, a.object_id, o.kind
           from document_object_assignments a join documents d on d.id = a.document_id
           left join document_objects o on o.id = a.object_id
           where d.project_id = %s and (a.object_id = '@document' or o.deleted_at is null)"""


def assignment_counts(connection, project_id: UUID) -> dict[str, dict[str, int]]:
    """{userId: {sections, blocks, documents}}；没有分工的成员不出现在结果里（调用方补 0）。
    标题对象和整篇（@document）算 sections，段落或表格算 blocks，涉及的文档数算 documents（17 §10.1 K-5）。"""
    return _count(connection.execute(_ROWS, (str(project_id),)).fetchall())


def clear_assignments(connection, project_id: UUID, user_id: UUID, actor_id: UUID | None) -> dict[str, int]:
    """删除 user_id 在本项目各文档中的显式分配，返回被清掉的数目。必须在调用方的写事务内执行（U-13）。"""
    from app import document_assignments as da

    rows = connection.execute(_ROWS + " and a.assignee_id = %s", (str(project_id), str(user_id))).fetchall()
    cleared = _count(rows).get(str(user_id), empty())
    by_document: dict[str, list[str]] = {}
    for row in rows:
        by_document.setdefault(str(row["document_id"]), []).append(str(row["object_id"]))
    for document_id, object_ids in by_document.items():
        connection.execute("delete from document_object_assignments where document_id = %s and assignee_id = %s",
                           (document_id, str(user_id)))
        da.bump(connection, document_id)
        da.event(connection, document_id, "assignment", "cleared", object_ids, user_id, actor_id)
    return cleared


def touch_project(connection, project_id: UUID) -> None:
    """项目负责人变化（projectOwners 变了）：各文档 assignments_revision + 1，前端随之重取归属（17 §10.1）。"""
    connection.execute("update documents set assignments_revision = assignments_revision + 1 where project_id = %s",
                       (str(project_id),))


def loses_editing(old_role: str | None, new_role: str | None) -> bool:
    """old → new（new=None 表示移出）是否让成员失去编辑权，需要清理分工。"""
    return old_role in EDITING_ROLES and new_role not in EDITING_ROLES
