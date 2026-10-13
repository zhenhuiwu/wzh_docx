"""导航权限、路由权限与后端接口权限一致（09 视觉分支审查 M1/M2/S6）。

前端的权限表在 web/src/access.ts，由 web/tests/access-check.mjs 做静态一致性检查并给出
每个角色可见的导航与可进入的路由；这里再用真实接口核对：导航可见的页面，其列表接口不能 403；
路由会显示“无权限”的页面，其列表接口确实 403。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.identity import create_user
from app.permissions import GLOBAL_ROLES, _ALL, permissions_for

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "web" / "tests" / "access-check.mjs"
PASSWORD = "correct-horse-1"

# 每个有权限要求的页面对应的列表接口。
LIST_API = {
    "test-cases": "/api/v1/test-tasks",
    "knowledge": "/api/v1/knowledge-bases",
    "templates": "/api/v1/templates",
    "users": "/api/v1/users",
    "writing-skills": "/api/v1/writing-skills",
    "projects": "/api/v1/projects",
}
# 项目列表对“仅项目成员”等没有 projects.read 的账号仍是 403（待 phase2 S1 按成员开放）；
# 前端对此显示“你还没有加入任何项目”，不显示错误和重试。
PENDING_PHASE2 = {"projects"}


def _node() -> str:
    """找 Node.js 22.6+（需要 --experimental-strip-types）。

    本地缺 Node 时跳过；设置 DOCWB_REQUIRE_NODE=1（CI 后端 job 已设置）时改为失败，
    避免一致性检查在 CI 里悄悄失效（09 复审 R2）。
    """
    node = shutil.which("node")
    problem = "" if node else "找不到 node"
    if node:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
        try:
            major, minor = (int(part) for part in version.lstrip("v").split(".")[:2])
        except ValueError:
            major, minor = 0, 0
        if (major, minor) < (22, 6):
            problem = f"Node.js 版本过低：{version or '未知'}"
    if problem:
        message = f"权限一致性检查需要 Node.js 22.6+（{problem}）"
        if os.environ.get("DOCWB_REQUIRE_NODE") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return node


def _run_check(roles: dict[str, list[str]]) -> dict:
    payload = json.dumps({"roles": roles, "all": sorted(_ALL)})
    done = subprocess.run(
        [_node(), "--experimental-strip-types", "--no-warnings", str(CHECK), payload],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr or done.stdout
    return json.loads(done.stdout)


def _role_sets() -> dict[str, list[str]]:
    sets = {role: permissions_for([role]) for role in GLOBAL_ROLES}
    sets["none"] = []
    return sets


def test_static_consistency_of_nav_and_routes() -> None:
    result = _run_check(_role_sets())
    # 系统管理员看得到所有导航。
    assert set(result["system_admin"]["nav"]) == {
        "projects", "test-cases", "knowledge", "users", "templates", "writing-skills"
    }
    # 只有 system_admin 有 tests.read：别的角色不显示“测试用例管理”（M1）。
    for role in GLOBAL_ROLES:
        if role != "system_admin":
            assert "test-cases" not in result[role]["nav"], role
            assert "test-cases" not in result[role]["routes"], role
    # 项目创建者有 templates.read：模板页只读可进，导航只给维护者（M2）。
    assert "templates" in result["project_creator"]["routes"]
    assert "templates" not in result["project_creator"]["nav"]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        pytest.fail("DATABASE_URL is required and has no default")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_ACCOUNT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("FILE_DATA_DIR", str(tmp_path))
    from app.config import clear_settings_cache

    clear_settings_cache()
    migrate()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    clear_settings_cache()


def _account(role: str) -> str:
    return f"access-{role.replace('_', '-')}"


def _ensure_user(role: str) -> None:
    account = _account(role)
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                "select id from users where account_key = %s", (account,)
            ).fetchone()
            if row:
                connection.execute("delete from sessions where user_id = %s", (row["id"],))
                connection.execute("delete from user_roles where user_id = %s", (row["id"],))
                connection.execute("delete from users where id = %s", (row["id"],))
    roles = [] if role == "none" else [role]
    create_user(account=account, name=role, password=PASSWORD, roles=roles, allow_no_roles=True)


def test_visible_nav_matches_backend_permissions(client: TestClient) -> None:
    result = _run_check(_role_sets())
    for role, seen in result.items():
        _ensure_user(role)
        session = TestClient(client.app)
        csrf = session.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        login = session.post(
            "/api/v1/auth/login",
            json={"account": _account(role), "password": PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code == 200, login.text
        for page, api in LIST_API.items():
            status = session.get(api).status_code
            if page in seen["routes"]:
                if page in PENDING_PHASE2 and status == 403:
                    continue
                assert status == 200, f"{role} 能进入 {page}，但 {api} 返回 {status}"
            else:
                assert status == 403, f"{role} 进入 {page} 会显示无权限，但 {api} 返回 {status}"
            if page in seen["nav"] and page not in PENDING_PHASE2:
                assert status == 200, f"{role} 看得到导航 {page}，但 {api} 返回 {status}"
