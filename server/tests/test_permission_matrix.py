"""测试复测 I6 / I7（测试工程师决定，待用户确认）权限矩阵。

- I6：全局 auditor 是全局只读审计——非成员也能读项目、成员列表、文档、预览、导出、下载文件；所有写操作 403。
- I7：看不到项目的账号（非成员、无 auditor/system_admin）对项目的写操作（修改、归档、取消归档、成员调整）一律 404，
  与读一致，不泄露项目是否存在；看得到但权限不足（成员、auditor）仍 403。
"""

from uuid import uuid4

import pytest

from app.db import connect
from app.identity import create_user
from tests.test_document_access import PASSWORD, _as, _login, members  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

ROLES = ("owner", "editor", "viewer", "outsider", "creator", "auditor", "nobody")
READERS = {"owner", "editor", "viewer", "auditor"}


@pytest.fixture
def matrix(members):  # noqa: F811
    client, project, document, users = members
    with connect() as connection:
        for name, roles in (("creator", ["project_creator"]), ("auditor", ["auditor"]), ("nobody", [])):
            users[name] = create_user(account=f"m-{name}", name=f"矩阵{name}", password=PASSWORD, roles=roles,
                                      allow_no_roles=True, connection=connection)
        connection.commit()
    return client, project, document, users


def _call(client, role, method, path, body=None):
    headers = _as(client, role)
    return client.request(method, f"/api/v1{path}", json=body, headers=headers)


READS = {
    "项目-读": lambda p, d: f"/projects/{p}",
    "成员-列表": lambda p, d: f"/projects/{p}/members",
    "文档-列表": lambda p, d: f"/projects/{p}/documents",
    "文档-读": lambda p, d: f"/documents/{d}",
    "文档-预览": lambda p, d: f"/documents/{d}/preview",
    "冲突-摘要": lambda p, d: f"/documents/{d}/conflicts/summary",
}


@pytest.mark.parametrize("operation", sorted(READS))
def test_reads(matrix, operation):
    client, project, document, _ = matrix
    got = {role: _call(client, role, "GET", READS[operation](project, document["id"])).status_code for role in ROLES}
    assert got == {role: 200 if role in READERS else 404 for role in ROLES}, got


def test_export_and_download(matrix):
    client, _, document, _ = matrix
    got = {}
    for role in ROLES:
        exported = _call(client, role, "POST", f"/documents/{document['id']}/export", {"expectedRevision": 0})
        got[role] = [exported.status_code]
        if exported.status_code == 200:
            got[role].append(client.get(f"/api/v1/files/{exported.json()['data']['fileId']}/content").status_code)
    assert got == {role: [200, 200] if role in READERS else [404] for role in ROLES}, got


def test_document_writes(matrix):
    client, _, document, _ = matrix
    got = {role: _call(client, role, "PATCH", f"/documents/{document['id']}/content",
                       {"expectedRevision": 0, "markdownContent": "# 改\n"}).status_code
           for role in ("viewer", "auditor", "outsider", "creator", "nobody")}
    assert got == {"viewer": 403, "auditor": 403, "outsider": 404, "creator": 404, "nobody": 404}, got


def _revision(client, project):
    client.cookies.clear()
    _login(client, "m-owner")
    return client.get(f"/api/v1/projects/{project}").json()["data"]["revision"]


@pytest.mark.parametrize("operation", ["修改", "归档", "取消归档", "成员-改角色", "成员-移除"])
def test_project_writes_hidden_projects_are_404(matrix, operation):
    client, project, _, users = matrix
    revision = _revision(client, project)
    target = users["viewer"]
    requests = {
        "修改": ("PATCH", f"/projects/{project}", {"expectedRevision": revision, "description": "x"}),
        "归档": ("POST", f"/projects/{project}/archive", {"expectedRevision": revision, "reason": "矩阵测试归档"}),
        "取消归档": ("POST", f"/projects/{project}/unarchive", {"expectedRevision": revision, "reason": "矩阵测试取消"}),
        "成员-改角色": ("PUT", f"/projects/{project}/members/{target}",
                     {"userId": str(target), "role": "editor", "expectedProjectRevision": revision}),
        "成员-移除": ("DELETE", f"/projects/{project}/members/{target}", None),
    }
    method, path, body = requests[operation]
    got = {role: _call(client, role, method, path, body).status_code
           for role in ("editor", "viewer", "auditor", "outsider", "creator", "nobody")}
    assert got == {"editor": 403, "viewer": 403, "auditor": 403, "outsider": 404, "creator": 404, "nobody": 404}, got
    # 不存在的项目：有 projects.manage 的非成员同样 404
    assert _call(client, "creator", method, path.replace(str(project), str(uuid4())), body).status_code == 404


def test_auditor_sees_all_projects_in_list(matrix):
    client, project, _, _ = matrix
    _as(client, "auditor")
    assert str(project) in {item["id"] for item in client.get("/api/v1/projects").json()["data"]["items"]}
    _as(client, "outsider")
    assert str(project) not in {item["id"] for item in client.get("/api/v1/projects").json()["data"]["items"]}


@pytest.mark.parametrize("path", ["facts", "knowledge-bindings"])
def test_auditor_reads_facts_and_bindings(matrix, path):
    """终验回归（低）：全局只读审计口径——审计员非成员也能读事实列表和知识绑定，不能改。"""
    client, project, _, _ = matrix
    assert _call(client, "auditor", "GET", f"/projects/{project}/{path}").status_code == 200
    assert _call(client, "nobody", "GET", f"/projects/{project}/{path}").status_code in (403, 404)
    assert _call(client, "auditor", "PUT", f"/projects/{project}/knowledge-bindings",
                 {"baseIds": [], "expectedProjectRevision": 0}).status_code == 403
