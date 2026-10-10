"""S4：对象读取、修改记录、版本操作人、REVISION_CONFLICT 最后修改人（01 §8.2 用例 37、43、44、46）。"""

from uuid import UUID

import pytest

from app.db import connect
from app.document_location import location_text
from app.document_objects import check_invariants, locations_of, live_objects
from app.markdown_blocks import split
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

TEXT = ("前言段。\n\n# 1 范围\n\n范围正文。\n\n## 1.1 标识\n\n标识正文。\n\n- 列一\n- 列二\n\n# 2 引用\n\n"
        "表 1 引用文件\n\n| 名称 | 编号 |\n| --- | --- |\n| 甲 | 1 |\n\n引用正文一。\n\n引用正文二。\n")


@pytest.fixture
def team(members, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.documents._check_word", lambda *args, **kwargs: None)
    client, project, document, users = members
    headers = _as(client, "owner")
    saved = client.patch(f"/api/v1/documents/{document['id']}/content",
                         json={"expectedRevision": 0, "markdownContent": TEXT}, headers=headers)
    assert saved.status_code == 200, saved.text
    return client, document["id"], users


def _save(client, did, revision, text, headers):
    return client.patch(f"/api/v1/documents/{did}/content", json={"expectedRevision": revision, "markdownContent": text},
                        headers=headers)


def test_locations_follow_direct_children_rules():
    # 用例 43：子标题下的块不计入父标题；同节列表连续计数；表题 label；第一个标题之前 headingPath=[]
    _, blocks = split(TEXT)
    locations = locations_of([(b.kind, b.body, b.heading_level) for b in blocks])
    by_body = {b.body: loc for b, loc in zip(blocks, locations)}
    assert by_body["前言段。"] == {"headingPath": [], "ordinal": 1, "kind": "paragraph", "label": None}
    assert by_body["范围正文。"]["ordinal"] == 1 and by_body["范围正文。"]["headingPath"] == ["1 范围"]
    assert by_body["标识正文。"]["headingPath"] == ["1 范围", "1.1 标识"] and by_body["标识正文。"]["ordinal"] == 1
    assert by_body["- 列二"]["ordinal"] == 2 and by_body["- 列二"]["kind"] == "list_item"
    table = next(loc for b, loc in zip(blocks, locations) if b.kind == "table")
    assert table["label"] == "表 1 引用文件" and table["headingPath"] == ["2 引用"]
    assert by_body["引用正文二。"]["ordinal"] == 3      # 表题段落也是直属段落
    assert "2 引用" in location_text(by_body["引用正文二。"])


def test_objects_since_revision_with_deletions_and_changed_by(team):
    # 用例 44
    client, did, users = team
    editor = _as(client, "editor")
    base = client.get(f"/api/v1/documents/{did}/objects").json()["data"]
    assert base["documentRevision"] == 1 and base["changedBy"] == []
    ids = {item["markdown"]: item["id"] for item in base["objects"]}
    text = TEXT.replace("标识正文。\n\n", "").replace("引用正文一。", "编写人员改正文一。")
    assert _save(client, did, 1, text, editor).status_code == 200
    reviewer_view = _as(client, "viewer")
    assert reviewer_view
    since = client.get(f"/api/v1/documents/{did}/objects", params={"sinceRevision": 1}).json()["data"]
    assert since["documentRevision"] == 2
    changed = {item["id"]: item for item in since["objects"]}
    assert set(changed) == {ids["标识正文。"], ids["引用正文一。"]}
    deleted = changed[ids["标识正文。"]]
    assert deleted["deleted"] is True and deleted["deletedBy"]["name"] == "成员editor" and deleted["deletedAt"]
    assert deleted["location"]["headingPath"] == ["1 范围", "1.1 标识"] and deleted["location"]["ordinal"] == 1
    updated = changed[ids["引用正文一。"]]
    assert updated["markdown"] == "编写人员改正文一。" and updated["revision"] == 1
    assert updated["updatedBy"]["name"] == "成员editor" and updated["afterObjectId"]
    assert since["changedBy"] == [{"id": str(users["editor"]), "name": "成员editor", "objects": 2}]
    # 自己不计入 changedBy
    _as(client, "editor")
    assert client.get(f"/api/v1/documents/{did}/objects", params={"sinceRevision": 1}).json()["data"]["changedBy"] == []
    with connect() as connection:
        assert check_invariants(connection, UUID(did))


def test_versions_and_changes_record_actor(team):
    # 用例 46：/versions createdBy / changedObjects；112 之前的版本为 null；/changes?clientSaveKey=
    client, did, users = team
    editor = _as(client, "editor")
    text = TEXT.replace("范围正文。", "范围正文改。").replace("引用正文二。", "引用正文二改。")
    assert _save(client, did, 1, text, editor).status_code == 200
    versions = client.get(f"/api/v1/documents/{did}/versions").json()["data"]["items"]
    by_revision = {item["revision"]: item for item in versions}
    assert by_revision[2]["createdBy"] == {"id": str(users["editor"]), "name": "成员editor"}
    assert by_revision[2]["changedObjects"] == 2
    assert by_revision[0]["createdBy"] is None and by_revision[0]["changedObjects"] is None
    changes = client.get(f"/api/v1/documents/{did}/changes").json()["data"]
    assert [item["docRevision"] for item in changes] == [2, 1]
    assert changes[0]["actor"]["name"] == "成员editor" and changes[0]["changedObjects"] == 2
    assert {item["op"] for item in changes[0]["objects"]} == {"update"}
    detail = client.get(f"/api/v1/documents/{did}/changes/{changes[0]['changeId']}").json()["data"]
    assert {(item["before"], item["after"]) for item in detail["objects"]} == {
        ("范围正文。", "范围正文改。"), ("引用正文二。", "引用正文二改。")}
    with connect() as connection:
        connection.execute("UPDATE document_changes SET client_save_key='keepalive-1' WHERE id=%s", (changes[0]["changeId"],))
        connection.commit()
    found = client.get(f"/api/v1/documents/{did}/changes", params={"clientSaveKey": "keepalive-1"}).json()["data"]
    assert [item["changeId"] for item in found] == [changes[0]["changeId"]]
    _as(client, "owner")      # 只返回本人的
    assert client.get(f"/api/v1/documents/{did}/changes", params={"clientSaveKey": "keepalive-1"}).json()["data"] == []


def test_revision_conflict_names_last_actor(team):
    # 用例 37：严格接口抽样
    client, did, users = team
    editor = _as(client, "editor")
    assert _save(client, did, 1, TEXT.replace("前言段。", "前言段改。"), editor).status_code == 200
    owner = _as(client, "owner")
    for response in (
        _save(client, did, 1, TEXT, owner),
        client.post(f"/api/v1/documents/{did}/versions/0/restore", json={"expectedRevision": 1}, headers=owner),
        client.post(f"/api/v1/documents/{did}/export", json={"expectedRevision": 1}, headers=owner),
        client.post(f"/api/v1/documents/{did}/headings", json={"expectedRevision": 1, "title": "新章",
                                                              "afterIndex": 0, "placement": "sibling"}, headers=owner),
    ):
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "REVISION_CONFLICT" and error["currentRevision"] == 2
        assert error["details"]["lastActor"] == {"id": str(users["editor"]), "name": "成员editor"}
        assert error["details"]["lastActorIsMe"] is False and error["details"]["lastUpdatedAt"]
    editor = _as(client, "editor")
    mine = _save(client, did, 1, TEXT, editor).json()["error"]
    assert mine["details"]["lastActorIsMe"] is True


def test_revision_conflict_before_112_has_no_actor(document_context):  # noqa: F811
    from tests.test_documents import create_document
    client, headers, *_ = document_context
    document = create_document(document_context)
    with connect() as connection:
        connection.execute("UPDATE documents SET revision=revision+1, updated_by=NULL WHERE id=%s", (document["id"],))
        connection.commit()
    response = client.patch(f"/api/v1/documents/{document['id']}/content",
                            json={"expectedRevision": 0, "markdownContent": document["markdownContent"]}, headers=headers)
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["lastActor"] is None and details["lastActorIsMe"] is False and details["lastUpdatedAt"]


def test_objects_endpoint_access(team):
    client, did, _ = team
    _as(client, "outsider")
    assert client.get(f"/api/v1/documents/{did}/objects").status_code == 404
    assert client.get(f"/api/v1/documents/{did}/changes").status_code == 404
    _as(client, "viewer")
    assert client.get(f"/api/v1/documents/{did}/objects").status_code == 200
    with connect() as connection:
        assert len(live_objects(connection, UUID(did))) == len(split(TEXT)[1])
