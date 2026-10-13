"""基线：建立/历史/对比/回退（回退记为新基线）与「我的编写」。"""

import pytest

from app.db import connect
from app.document_baselines import diff_sections, join_sections, split_sections, _current_sections
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401


@pytest.fixture
def bteam(team):  # noqa: F811
    return team


def test_split_roundtrip_and_fence():
    text = "前言\n\n# A\n\n段1\n\n```\n# 不是标题\n```\n\n## B\n\n段2\n"
    sections = split_sections(text)
    assert [s["heading"] for s in sections] == ["", "A", "B"]
    assert join_sections([s["content"] for s in sections]) == text


def test_diff_large_document_section_level():
    old = "".join(f"# 第{i}章\n\n正文{i}。\n\n" for i in range(2000))
    new = old.replace("正文7。", "改过7。").replace("# 第9章\n\n正文9。\n\n", "") + "# 新章\n\n新段。\n"
    changes, stats = diff_sections(_current_sections(old), _current_sections(new))
    assert stats["modified"] == 1 and stats["removed"] == 1 and stats["added"] == 1
    modified = next(c for c in changes if c["type"] == "modified")
    assert modified["paragraphs"] == [{"op": "replace", "old": "正文7。", "new": "改过7。"}]


def test_baseline_lifecycle(bteam):
    client, did, users = bteam
    owner = _as(client, "owner")
    first = client.post(f"/api/v1/documents/{did}/baselines", headers=owner, json={"message": "初稿"})
    assert first.status_code == 201, first.text
    assert first.json()["data"]["label"] == "B1"
    editor = _as(client, "editor")
    assert client.post(f"/api/v1/documents/{did}/baselines", headers=editor, json={}).status_code == 403
    revision = current(client, did)["revision"]
    assert save(client, did, revision, TEXT.replace("第二段。", "改后第二段。"), editor).status_code == 200
    owner = _as(client, "owner")
    assert client.post(f"/api/v1/documents/{did}/baselines", headers=owner, json={"message": "二稿"}).status_code == 201
    diff = client.get(f"/api/v1/documents/{did}/baselines-diff", params={"base": 1, "compare": "2"}).json()["data"]
    assert diff["stats"]["modified"] == 1
    assert diff["items"][0]["paragraphs"] == [{"op": "replace", "old": "第二段。", "new": "改后第二段。"}]
    vs_current = client.get(f"/api/v1/documents/{did}/baselines-diff", params={"base": 2}).json()["data"]
    assert vs_current["total"] == 0
    doc = current(client, did)
    reverted = client.post(f"/api/v1/documents/{did}/baselines/1/revert", headers=owner,
                           json={"expectedRevision": doc["revision"]})
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["data"]["kind"] == "revert" and reverted.json()["data"]["revertedFromSeq"] == 1
    assert current(client, did)["markdownContent"] == TEXT
    history = client.get(f"/api/v1/documents/{did}/baselines", params={"limit": 2}).json()["data"]
    assert history["total"] == 3 and history["hasMore"] and [i["seq"] for i in history["items"]] == [3, 2]
    with connect() as connection:  # 相同章节只存一份
        blobs = connection.execute("SELECT count(*) AS n FROM document_baseline_blobs").fetchone()["n"]
    assert blobs == 3
    stale = client.post(f"/api/v1/documents/{did}/baselines/2/revert", headers=owner, json={"expectedRevision": 0})
    assert stale.status_code == 409


def test_my_writing_lists_owned_or_assigned(bteam):
    client, did, users = bteam
    with connect() as connection:
        connection.execute("UPDATE documents SET owner_id=%s WHERE id=%s", (users["editor"], did))
        connection.commit()
    # 验收 P1(a)：项目负责人即使不是文档负责人，也能在「我的编写」里看到项目的文档（不会是空页）
    _as(client, "owner")
    owned = client.get("/api/v1/my-writing").json()["data"]["items"]
    assert [(item["id"], item["myRole"]) for item in owned] == [(did, "owner")]
    _as(client, "editor")
    data = client.get("/api/v1/my-writing").json()["data"]
    assert did in [item["id"] for item in data["items"]]
    _as(client, "outsider")
    assert client.get("/api/v1/my-writing").json()["data"]["items"] == []


def test_project_baseline_tags_documents(bteam):
    client, did, users = bteam
    with connect() as connection:
        project = connection.execute("SELECT project_id FROM documents WHERE id=%s", (did,)).fetchone()["project_id"]
    owner = _as(client, "owner")
    created = client.post(f"/api/v1/projects/{project}/baselines", headers=owner, json={"name": "评审前", "documentIds": [did]})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["documentCount"] == 1
    editor = _as(client, "editor")
    assert client.post(f"/api/v1/projects/{project}/baselines", headers=editor,
                       json={"name": "x", "documentIds": [did]}).status_code == 403
    doc = current(client, did)
    assert save(client, did, doc["revision"], TEXT + "\n新增段。\n", editor).status_code == 200
    detail = client.get(f"/api/v1/projects/{project}/baselines/1").json()["data"]
    assert detail["documents"][0]["changed"] and detail["documents"][0]["sinceBaseline"]["paragraphsAdded"] == 1
    assert client.get(f"/api/v1/projects/{project}/baselines").json()["data"]["total"] == 1


def test_my_writing_hidden_for_read_only_members(bteam):
    client, did, users = bteam
    for role, visible in (("editor", True), ("owner", True), ("viewer", False), ("reviewer", False), ("outsider", False)):
        _as(client, role)
        assert client.get("/api/v1/my-writing/access").json()["data"]["visible"] is visible, role
