"""文档访问：项目成员可见性 + 项目角色权限（阶段2 S1，01 §4.6/§4.7/§7.2）。

- 系统管理员：全部可见、全部可做。
- 其他账号：必须是项目成员（project_members），否则 404，不泄露项目或文档是否存在、也不给负责人姓名。
- 是成员但项目角色没有对应权限：403 FORBIDDEN，details = {projectRole, ownerName}。
- 文档读写不再要求全局 documents.* 权限（决策1）。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Request

from app.errors import AppError
from app import identity
from app.identity import Principal
from app.permissions import project_permissions

Need = Literal["read", "edit", "export"]

_PERMISSION = {"read": "documents.read", "edit": "documents.edit", "export": "documents.export"}
_NOT_FOUND_PROJECT = "项目不存在或你无权访问。"
_NOT_FOUND_DOCUMENT = "文档不存在或你无权访问。"


@dataclass(frozen=True)
class DocumentRow:
    """_document_row 的结果。字段名与列名一致；为兼容旧调用方仍支持 0..15 下标。"""

    id: UUID
    project_id: UUID
    name: str
    source_type: str
    template_document_id: UUID | None
    original_docx_file_id: UUID | None
    format_template: dict
    markdown_content: str
    outline: list
    status: str
    owner_id: UUID
    owner_name: str
    revision: int
    updated_at: datetime
    project_name: str
    template_document_name: str | None
    # 以下为按名访问的新列，不参与下标（追加列不会让旧下标错位）。
    project_role: str | None = None
    admin: bool = False

    def __getitem__(self, key: int | str):
        if isinstance(key, str):
            return getattr(self, key)
        return getattr(self, _LEGACY[key].name)

    def __len__(self) -> int:
        return len(_LEGACY)

    def __iter__(self):
        return (getattr(self, item.name) for item in _LEGACY)


_LEGACY = fields(DocumentRow)[:16]

_SELECT = """
    SELECT d.id, d.project_id, d.name, d.source_type, d.template_document_id,
           d.original_docx_file_id, d.format_template, d.markdown_content, d.outline,
           d.status, d.owner_id, owner.name as owner_name, d.revision, d.updated_at,
           p.name as project_name,
           td.name as template_document_name,
           p.owner_id as project_owner_id
    FROM documents d
    JOIN projects p ON p.id = d.project_id
    JOIN users owner ON owner.id = d.owner_id
    LEFT JOIN template_documents td ON td.id = d.template_document_id
    WHERE d.id = %s
"""


def is_admin(principal: Principal) -> bool:
    return "system_admin" in principal.roles


def document_principal(request: Request) -> Principal:
    """登录 + 写请求 CSRF；具体权限由项目角色决定（不看全局 documents.*）。"""
    principal = identity.require_session(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        identity.require_csrf(request, principal)
    from app.document_objects import set_actor
    set_actor(principal.user_id)
    return principal


def member_role(connection, project_id: UUID, user_id: UUID) -> str | None:
    row = connection.execute(
        "select role from project_members where project_id = %s and user_id = %s",
        (project_id, user_id),
    ).fetchone()
    return None if row is None else str(row["role"])


def _owner_name(connection, project_id: UUID) -> str | None:
    row = connection.execute(
        """select u.name from project_members m join users u on u.id = m.user_id
           where m.project_id = %s and m.role = 'owner' order by u.name limit 1""",
        (project_id,),
    ).fetchone()
    return None if row is None else str(row["name"])


def check_project(connection, project_id: UUID, role: str | None, admin: bool, need: Need,
                  missing: str = _NOT_FOUND_PROJECT) -> None:
    if admin:
        return
    if role is None:
        raise AppError(404, "NOT_FOUND", missing)
    if _PERMISSION[need] not in project_permissions(role):
        action = {"read": "查看", "edit": "修改", "export": "导出"}[need]
        owner = _owner_name(connection, project_id)
        tail = f"如需开通请联系负责人 {owner}。" if owner else "如需开通请联系项目负责人。"
        raise AppError(
            403,
            "FORBIDDEN",
            f"你在该项目中没有{action}文档的权限，{tail}",
            details={"projectRole": role, "ownerName": owner},
        )


def project_access(connection, principal: Principal, project_id: UUID, need: Need = "read") -> str | None:
    """检查项目级文档权限，返回项目角色（管理员不是成员时为 None）。"""
    exists = connection.execute("select id from projects where id = %s", (project_id,)).fetchone()
    admin = is_admin(principal)
    role = member_role(connection, project_id, principal.user_id)
    if exists is None:
        raise AppError(404, "NOT_FOUND", _NOT_FOUND_PROJECT)
    check_project(connection, project_id, role, admin, need, _NOT_FOUND_PROJECT)
    return role


def document_access(connection, principal: Principal, document_id: UUID, need: Need = "read") -> DocumentRow:
    """读取文档并检查权限（01 §7.2 第1、2条）。"""
    return load_document(connection, document_id, principal.user_id, is_admin(principal), need)


def load_document(connection, document_id: UUID, user_id: UUID, admin: bool, need: Need = "read") -> DocumentRow:
    row = connection.execute(_SELECT, (document_id,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", _NOT_FOUND_DOCUMENT)
    role = member_role(connection, row["project_id"], user_id)
    check_project(connection, row["project_id"], role, admin, need, _NOT_FOUND_DOCUMENT)
    values = {item.name: row[item.name] for item in _LEGACY}
    return DocumentRow(**values, project_role=role, admin=admin)


def permissions_of(role: str | None, admin: bool) -> dict[str, object]:
    """4.7 permissions 字段（仅展示；写接口仍独立检查）。"""
    if admin:
        return {"projectRole": role, "canEdit": True, "canExport": True, "canResolveConflicts": True,
                "canViewMemberConflicts": True, "readonlyReason": None}
    granted = project_permissions(role) if role else frozenset()
    can_edit = "documents.edit" in granted
    return {
        "projectRole": role,
        "canEdit": can_edit,
        "canExport": "documents.export" in granted,
        "canResolveConflicts": can_edit,
        "canViewMemberConflicts": role == "owner",
        "readonlyReason": None if can_edit else "role",
    }
