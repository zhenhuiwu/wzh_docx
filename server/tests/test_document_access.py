"""S1：项目成员可见性与项目角色权限（01 §8.2 用例 20、35、36）。"""

from uuid import uuid4

import pytest

from app.db import connect, migrate
from app.files import store_bytes
from app.identity import create_user
from tests.test_documents import document_context  # noqa: F401  (fixture reuse)

PASSWORD = "member-test-password"


def _login(client, account):
    token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
    response = client.post("/api/v1/auth/login", json={"account": account, "password": PASSWORD}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["data"]["csrfToken"]}


@pytest.fixture
def members(document_context):  # noqa: F811
    client, admin_headers, project, other, stored, _ = document_context
    created = client.post(f"/api/v1/projects/{project}/documents",
                          json={"name": "成员文档", "sourceType": "upload", "wordFileId": str(stored.id)}, headers=admin_headers)
    assert created.status_code == 200, created.text
    document = created.json()["data"]
    users = {}
    with connect() as connection:
        for role in ("owner", "editor", "reviewer", "viewer", "outsider"):
            # 全局角色没有任何 documents./projects.* 权限：只靠项目角色（决策1）。
            uid = create_user(account=f"m-{role}", name=f"成员{role}", password=PASSWORD, roles=["template_manager"], connection=connection)
            users[role] = uid
            if role != "outsider":
                connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,%s)", (project, uid, role))
        connection.commit()
    return client, project, document, users


def _as(client, role):
    client.cookies.clear()
    return _login(client, f"m-{role}")


def test_editor_without_global_role_can_list_read_and_save(members):
    client, project, document, _ = members
    headers = _as(client, "editor")
    listing = client.get(f"/api/v1/projects/{project}/documents")
    assert listing.status_code == 200, listing.text
    assert [item["name"] for item in listing.json()["data"]["items"]] == ["成员文档"]
    assert client.get(f"/api/v1/projects/{project}").status_code == 200
    detail = client.get(f"/api/v1/documents/{document['id']}")
    assert detail.status_code == 200
    content = detail.json()["data"]["markdownContent"].replace("原始正文。", "编写人员修改。")
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": content}, headers=headers)
    assert saved.status_code == 200, saved.text
    exported = client.post(f"/api/v1/documents/{document['id']}/export", json={"expectedRevision": 1}, headers=headers)
    assert exported.status_code == 200, exported.text
    assert client.get(f"/api/v1/files/{exported.json()['data']['fileId']}/content").status_code == 200


def test_viewer_reads_and_exports_but_cannot_write(members):
    client, project, document, _ = members
    headers = _as(client, "viewer")
    # N-S5：成员（含只读）在“文档编写”页签能列出文档，不再出现“项目不存在”。
    listing = client.get(f"/api/v1/projects/{project}/documents")
    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["permissions"]["canEdit"] is False
    assert client.get(f"/api/v1/documents/{document['id']}").status_code == 200
    denied = client.patch(f"/api/v1/documents/{document['id']}/content",
                          json={"expectedRevision": 0, "markdownContent": "x"}, headers=headers)
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["details"] == {"projectRole": "viewer", "ownerName": "成员owner"}
    assert "成员owner" in error["message"]
    exported = client.post(f"/api/v1/documents/{document['id']}/export", json={"expectedRevision": 0}, headers=headers)
    assert exported.status_code == 200, exported.text


def test_outsider_gets_404_without_names(members):
    client, project, document, _ = members
    headers = _as(client, "outsider")
    for response in (
        client.get(f"/api/v1/projects/{project}/documents"),
        client.get(f"/api/v1/documents/{document['id']}"),
        client.get(f"/api/v1/documents/{document['id']}/preview"),
        client.patch(f"/api/v1/documents/{document['id']}/content", json={"expectedRevision": 0, "markdownContent": "x"}, headers=headers),
    ):
        assert response.status_code == 404, response.text
        body = response.text
        assert "成员文档" not in body and "成员owner" not in body and "文档测试" not in body
        assert response.json()["error"]["message"] in {"项目不存在或你无权访问。", "文档不存在或你无权访问。"}
    missing = client.get(f"/api/v1/projects/{uuid4()}/documents")
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "项目不存在或你无权访问。"


def test_removed_member_loses_access(members):
    client, project, document, users = members
    headers = _as(client, "editor")
    assert client.get(f"/api/v1/documents/{document['id']}").status_code == 200
    with connect() as connection:
        connection.execute("DELETE FROM project_members WHERE project_id=%s AND user_id=%s", (project, users["editor"]))
        connection.commit()
    response = client.patch(f"/api/v1/documents/{document['id']}/content",
                            json={"expectedRevision": 0, "markdownContent": "x"}, headers=headers)
    assert response.status_code == 404


# 用例 36：同一参数表同时驱动“字段值”和“实际调用结果”。
ROLES = {
    "owner": dict(canEdit=True, canExport=True, canResolveConflicts=True, canViewMemberConflicts=True, readonlyReason=None),
    "editor": dict(canEdit=True, canExport=True, canResolveConflicts=True, canViewMemberConflicts=False, readonlyReason=None),
    "reviewer": dict(canEdit=False, canExport=True, canResolveConflicts=False, canViewMemberConflicts=False, readonlyReason="role"),
    "viewer": dict(canEdit=False, canExport=True, canResolveConflicts=False, canViewMemberConflicts=False, readonlyReason="role"),
    "admin": dict(canEdit=True, canExport=True, canResolveConflicts=True, canViewMemberConflicts=True, readonlyReason=None),
}


@pytest.mark.parametrize("role", list(ROLES))
def test_permissions_field_matches_real_calls(members, document_context, role):  # noqa: F811
    client, project, document, _ = members
    if role == "admin":
        client.cookies.clear()
        token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        login = client.post("/api/v1/auth/login", json={"account": "document-owner", "password": "document-test-password"},
                            headers={"X-CSRF-Token": token})
        headers = {"X-CSRF-Token": login.json()["data"]["csrfToken"]}
    else:
        headers = _as(client, role)
    expected = ROLES[role]
    detail = client.get(f"/api/v1/documents/{document['id']}").json()["data"]
    permissions = detail["permissions"]
    assert {key: permissions[key] for key in expected} == expected
    assert permissions["projectRole"] == (None if role == "admin" else role)
    listing = client.get(f"/api/v1/projects/{project}/documents").json()["data"]
    assert listing["items"][0]["permissions"] == permissions == listing["permissions"]
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": detail["revision"], "markdownContent": detail["markdownContent"] + "\n\n补充。"},
                         headers=headers)
    assert (saved.status_code == 200) == expected["canEdit"], saved.text
    if not expected["canEdit"]:
        assert saved.status_code == 403
    revision = detail["revision"] + (1 if expected["canEdit"] else 0)
    exported = client.post(f"/api/v1/documents/{document['id']}/export", json={"expectedRevision": revision}, headers=headers)
    assert (exported.status_code == 200) == expected["canExport"], exported.text


def test_project_files_need_membership(document_context):  # noqa: F811
    client, _, project, other, _, source = document_context
    migrate()
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    stored = store_bytes(name="成员附件.docx", media_type=media, purpose="attachment", project_id=other, content=source)
    mine = store_bytes(name="本项目附件.docx", media_type=media, purpose="attachment", project_id=project, content=source)
    with connect() as connection:
        uid = create_user(account="m-file", name="文件成员", password=PASSWORD, roles=["template_manager"], connection=connection)
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'viewer')", (project, uid))
        connection.commit()
    client.cookies.clear()
    _login(client, "m-file")
    assert client.get(f"/api/v1/files/{stored.id}/content").status_code in {403, 404}
    assert client.get(f"/api/v1/files/{mine.id}/content").status_code == 200


def test_project_files_global_files_read_not_cross_project(document_context):  # noqa: F811
    """审查 S9（收紧，待用户确认）：全局 files.read 不跨项目；system_admin、auditor 可读；非项目文件仍按 files.read。"""
    client, _, project, _other, _, source = document_context
    migrate()
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    project_file = store_bytes(name="项目原稿.docx", media_type=media, purpose="attachment", project_id=project, content=source)
    loose = store_bytes(name="非项目模板.docx", media_type=media, purpose="template", project_id=None, content=source)
    with connect() as connection:
        for account, roles in (("s9-creator", ["project_creator"]), ("s9-tm", ["template_manager"]),
                               ("s9-km", ["knowledge_manager"]), ("s9-audit", ["auditor"]), ("s9-admin", ["system_admin"])):
            create_user(account=account, name=account, password=PASSWORD, roles=roles, connection=connection)
        connection.commit()
    for account in ("s9-creator", "s9-tm", "s9-km"):
        client.cookies.clear()
        _login(client, account)
        assert client.get(f"/api/v1/files/{project_file.id}/content").status_code == 404, account
        assert client.get(f"/api/v1/files/{project_file.id}/preview").status_code == 404, account
        if account != "s9-km":  # 模板文件另需 templates.read（原规则不变）
            assert client.get(f"/api/v1/files/{loose.id}/content").status_code == 200, account
    for account in ("s9-audit", "s9-admin"):
        client.cookies.clear()
        _login(client, account)
        assert client.get(f"/api/v1/files/{project_file.id}/content").status_code == 200, account
    client.cookies.clear()
    _login(client, "s9-audit")
    assert client.get(f"/api/v1/files/{loose.id}/content").status_code == 404  # auditor 没有 files.read：与非成员同为 404（复审 S9）


def test_file_status_does_not_leak_existence(document_context):  # noqa: F811
    """复审 S9：无全局角色的非成员访问真实项目文件与不存在的 id，状态码与文案完全相同（404）。"""
    client, _, project, _other, _, source = document_context
    migrate()
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    project_file = store_bytes(name="项目原稿.docx", media_type=media, purpose="attachment", project_id=project, content=source)
    loose = store_bytes(name="非项目模板.docx", media_type=media, purpose="template", project_id=None, content=source)
    with connect() as connection:
        create_user(account="s9-nobody", name="无角色", password=PASSWORD, roles=[], allow_no_roles=True, connection=connection)
        connection.commit()
    client.cookies.clear()
    _login(client, "s9-nobody")
    answers = []
    for file_id in (project_file.id, loose.id, uuid4()):
        for kind in ("content", "preview"):
            response = client.get(f"/api/v1/files/{file_id}/{kind}")
            answers.append((response.status_code, response.json()["error"]["code"], response.json()["error"]["message"]))
    assert set(answers) == {(404, "NOT_FOUND", "没有这个文件。")}, answers


def test_member_without_global_role_uploads_to_own_project_only(members):
    # P0-4（决策1）：新建用户全局角色默认 []，上传项目 Word 只看项目角色。
    from io import BytesIO
    from docx import Document as WordDocument
    client, project, _, _ = members
    buffer = BytesIO()
    WordDocument().save(buffer)
    files = {"file": ("成员上传.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    for role, expected in (("editor", 201), ("viewer", 403), ("outsider", 403)):
        headers = _as(client, role)
        response = client.post("/api/v1/files", files=files, data={"purpose": "attachment", "projectId": str(project)},
                               headers={**headers, "Idempotency-Key": f"member-upload-{role}"})
        assert response.status_code == expected, (role, response.text)
    headers = _as(client, "editor")
    unscoped = client.post("/api/v1/files", files=files, data={"purpose": "attachment"},
                           headers={**headers, "Idempotency-Key": "member-upload-unscoped"})
    assert unscoped.status_code == 422  # 夹具账号带 template_manager（含 files.upload），未选项目仍被拒


def test_project_detail_permissions_hide_edit_for_readonly_members(members):
    """测试 L2：只读成员拿到 canEdit=false，前端据此隐藏“编辑项目”。"""
    client, project, _document, _ = members
    for role, can_manage in (("viewer", False), ("reviewer", False), ("editor", False), ("owner", True)):
        _as(client, role)
        permissions = client.get(f"/api/v1/projects/{project}").json()["data"]["permissions"]
        assert permissions["projectRole"] == role
        assert permissions["canEdit"] is False  # 这些账号没有全局 projects.manage（与 PATCH 判定一致）
        assert permissions["canManageMembers"] is can_manage
