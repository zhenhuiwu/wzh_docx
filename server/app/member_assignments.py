"""A05 ↔ A07 的接口：成员在本项目文档中的显式分工（段落负责人）。

A07（迁移 118，document_object_assignments）尚未实现。A05 只在这里留两个挂钩，调用点都已放好：
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


def assignment_counts(connection, project_id: UUID) -> dict[str, dict[str, int]]:
    """{userId: {sections, blocks, documents}}；没有分工的成员不出现在结果里（调用方补 0）。"""
    del connection, project_id
    return {}


def clear_assignments(connection, project_id: UUID, user_id: UUID, actor_id: UUID | None) -> dict[str, int]:
    """删除 user_id 在本项目各文档中的显式分配，返回被清掉的数目。必须在调用方的写事务内执行。"""
    del connection, project_id, user_id, actor_id
    return empty()


def loses_editing(old_role: str | None, new_role: str | None) -> bool:
    """old → new（new=None 表示移出）是否让成员失去编辑权，需要清理分工。"""
    return old_role in EDITING_ROLES and new_role not in EDITING_ROLES
