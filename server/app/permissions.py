"""Fixed local role definitions.

Company approval policy is not settled. Unknown permissions are denied.
"""

from __future__ import annotations

GLOBAL_ROLES = (
    "system_admin",
    "project_creator",
    "template_manager",
    "knowledge_manager",
    "auditor",
)

_ALL = frozenset(
    {
        "actions.approve",
        "assistant.ask",
        "assistant.read",
        "assistant.write",
        "audit.export",
        "audit.read",
        "capture.execute",
        "deliveries.publish",
        "documents.edit",
        "documents.export",
        "documents.read",
        "evidence.manage",
        "evidence.read",
        "facts.confirm",
        "facts.edit",
        "files.read",
        "files.upload",
        "jobs.control",
        "jobs.read",
        "jobs.verify",
        "knowledge.access.manage",
        "knowledge.manage",
        "knowledge.publish",
        "knowledge.read",
        "projects.create",
        "projects.manage",
        "projects.members.manage",
        "projects.read",
        "reviews.decide",
        "reviews.read",
        "reviews.submit",
        "system.manage",
        "templates.manage",
        "templates.publish",
        "templates.read",
        "tests.approve",
        "tests.edit",
        "tests.execute",
        "tests.read",
        "tools.takeover",
        "users.manage",
        "users.read",
    }
)

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "system_admin": _ALL,
    "project_creator": frozenset(
        {
            "documents.edit",
            "documents.export",
            "documents.read",
            "files.read",
            "files.upload",
            "facts.edit",
            "jobs.read",
            "projects.create",
            "projects.manage",
            "projects.members.manage",
            "projects.read",
            "templates.read",
        }
    ),
    "template_manager": frozenset(
        {
            "files.read",
            "files.upload",
            "templates.manage",
            "templates.publish",
            "templates.read",
        }
    ),
    "knowledge_manager": frozenset(
        {
            "files.read",
            "files.upload",
            "knowledge.access.manage",
            "knowledge.manage",
            "knowledge.publish",
            "knowledge.read",
        }
    ),
    "auditor": frozenset(
        {
            "audit.export",
            "audit.read",
            "documents.read",
            "projects.read",
        }
    ),
}


def permissions_for(roles: list[str] | tuple[str, ...]) -> list[str]:
    granted: set[str] = set()
    for role in roles:
        granted.update(_ROLE_PERMISSIONS.get(role, frozenset()))
    return sorted(granted)


def has_permission(roles: list[str] | tuple[str, ...], permission: str) -> bool:
    return permission in permissions_for(roles)


_ROLE_TEXT: dict[str, tuple[str, str, str, bool]] = {
    "system_admin": ("系统管理员", "global", "管理账号、角色和系统设置。", True),
    "project_creator": ("项目创建者", "global", "创建项目并维护自己创建的项目。", True),
    "template_manager": ("模板维护者", "global", "维护模板和发布版本。", True),
    "knowledge_manager": ("知识维护者", "global", "维护知识库和资料文件。", True),
    "auditor": ("审计查看者", "global", "查看审计和项目只读信息。", True),
    "owner": ("项目负责人", "project", "负责一个项目的成员和内容。", True),
    "editor": ("编写或测试人员", "project", "编写项目内容。", True),
    "reviewer": ("审核人员", "project", "审核项目内容。", True),
    "viewer": ("只读成员", "project", "查看已授权的项目内容。", True),
}

_PROJECT_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset(
        {
            "documents.edit",
            "documents.read",
            "projects.manage",
            "projects.members.manage",
            "projects.read",
        }
    ),
    "editor": frozenset({"documents.edit", "documents.read"}),
    "reviewer": frozenset({"documents.read", "reviews.decide", "reviews.read"}),
    "viewer": frozenset({"documents.read", "projects.read"}),
}


def role_definitions() -> list[dict[str, object]]:
    """Read-only role catalog. Callers cannot add a role through this list."""

    items: list[dict[str, object]] = []
    for key, (name, scope, description, assignable) in _ROLE_TEXT.items():
        if scope == "global":
            keys = _ROLE_PERMISSIONS.get(key, frozenset())
        else:
            keys = _PROJECT_PERMISSIONS.get(key, frozenset())
        items.append(
            {
                "key": key,
                "name": name,
                "scope": scope,
                "description": description,
                "assignable": assignable,
                "permissions": [
                    {
                        "key": permission,
                        "name": permission,
                        "description": "这项能力由角色定义，不能在页面上单独增删。",
                    }
                    for permission in sorted(keys)
                ],
            }
        )
    return items
