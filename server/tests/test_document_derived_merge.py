"""S6：派生写入接口的 allowMerge（01 §8.2 用例 17、18）。

AI 章节/全文采纳、添加标题、图表插入在基线快照上计算新全文，再与当前正文三方合并；
恢复版本、撤回 AI 保持严格检查。测试结果补写见 test_test_document_results.py。
"""

import base64
import json
from uuid import UUID, uuid4

from app.db import connect
from app.document_diagrams import _diagram_path
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import TEXT, current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def _other_edits(client, did, old, new):
    """B（editor2）在 revision 1 上改一处，产生 revision 2。"""
    response = save(client, did, 1, TEXT.replace(old, new), _as(client, "editor2"))
    assert response.status_code == 200, response.text


def test_ai_section_adoption_merges_other_sections(team):
    # 用例 17：B 改了别的章节 → A 采纳 200 且 merged=true
    client, did, users = team
    _other_edits(client, did, "引用段。", "B 改引用段。")
    headers = _as(client, "editor")
    body = {"expectedRevision": 1, "headingIndex": 0, "content": "AI 范围正文。", "mode": "replace", "instruction": "补全范围"}
    strict = client.post(f"/api/v1/documents/{did}/sections/insert", json=body, headers=headers)
    assert strict.status_code == 409 and strict.json()["error"]["code"] == "REVISION_CONFLICT"
    adopted = client.post(f"/api/v1/documents/{did}/sections/insert", json={**body, "allowMerge": True, "clientSaveKey": "ai-section-1"},
                          headers=headers)
    assert adopted.status_code == 200, adopted.text
    data = adopted.json()["data"]
    assert data["merged"] is True and data["revision"] == 3
    assert "AI 范围正文。" in data["markdownContent"] and "B 改引用段。" in data["markdownContent"]
    assert "第一段。" not in data["markdownContent"]
    assert data["changedBy"][0]["name"] == "成员editor2"
    replay = client.post(f"/api/v1/documents/{did}/sections/insert", json={**body, "allowMerge": True, "clientSaveKey": "ai-section-1"},
                         headers=headers)
    assert replay.status_code == 200 and replay.json()["data"]["revision"] == 3
    with connect() as connection:
        change = connection.execute("SELECT kind, actor_id FROM document_changes WHERE document_id=%s AND doc_revision=3", (did,)).fetchone()
        assert change["kind"] == "ai_section" and str(change["actor_id"]) == str(users["editor"])
        memory = connection.execute("SELECT revision FROM document_memory_events WHERE document_id=%s", (did,)).fetchall()
        assert [row["revision"] for row in memory] == [3]


def test_ai_section_adoption_conflicts_on_same_section(team):
    # 用例 17：B 改了同一章节 → 409 OBJECT_CONFLICT，副本保留
    client, did, _ = team
    _other_edits(client, did, "第二段。", "B 改第二段。")
    headers = _as(client, "editor")
    rejected = client.post(f"/api/v1/documents/{did}/sections/insert", headers=headers, json={
        "expectedRevision": 1, "headingIndex": 0, "content": "AI 范围正文。", "mode": "replace", "allowMerge": True,
        "clientSaveKey": "ai-section-2"})
    assert rejected.status_code == 409, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "OBJECT_CONFLICT"
    assert "B 改第二段。" in current(client, did)["markdownContent"]
    listed = client.get(f"/api/v1/documents/{did}/conflicts").json()["data"]["items"]
    assert listed and any("B 改第二段。" in (item["theirs"]["markdown"] or "") for item in listed)


def test_document_edits_and_heading_merge(team):
    client, did, _ = team
    _other_edits(client, did, "第三段。", "B 改第三段。")
    headers = _as(client, "editor")
    whole = client.post(f"/api/v1/documents/{did}/edits", headers=headers, json={
        "expectedRevision": 1, "edits": [{"headingIndex": 1, "content": "全文编写的引用。"}], "allowMerge": True,
        "clientSaveKey": "ai-document-1"})
    assert whole.status_code == 200, whole.text
    assert whole.json()["data"]["merged"] is True
    text = whole.json()["data"]["markdownContent"]
    assert "全文编写的引用。" in text and "B 改第三段。" in text
    # 标题：仍以旧基线（revision 1）提交，合并后返回服务器全文里的标题序号
    heading = client.post(f"/api/v1/documents/{did}/headings", headers=headers, json={
        "expectedRevision": 1, "afterIndex": 0, "placement": "sibling", "title": "新章节", "allowMerge": True,
        "clientSaveKey": "heading-merge-1"})
    assert heading.status_code == 200, heading.text
    data = heading.json()["data"]
    assert data["merged"] is True and "# 新章节" in data["markdownContent"]
    assert "全文编写的引用。" in data["markdownContent"] and "B 改第三段。" in data["markdownContent"]
    assert data["headingIndex"] == 1
    strict = client.post(f"/api/v1/documents/{did}/headings", headers=headers, json={
        "expectedRevision": 1, "afterIndex": 0, "placement": "sibling", "title": "再一章"})
    assert strict.status_code == 409 and strict.json()["error"]["code"] == "REVISION_CONFLICT"


def test_diagram_insert_merges_and_keeps_images(team):
    client, did, _ = team
    gid = uuid4()
    with connect() as connection:
        connection.execute("INSERT INTO document_diagrams(id,document_id,name,kind,source_revision,graph_json) VALUES(%s,%s,%s,'flowchart',1,%s)",
                           (gid, did, "流程图", json.dumps({"nodes": [], "edges": []})))
        connection.commit()
    picture = _diagram_path(UUID(str(gid)), "diagram.png")
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(PNG)
    _other_edits(client, did, "引用段。", "B 改引用段。")
    headers = _as(client, "editor")
    path = f"/api/v1/documents/{did}/diagrams/{gid}/insert"
    assert client.post(path, json={"expectedRevision": 1, "headingIndex": 0}, headers=headers).status_code == 409
    body = {"expectedRevision": 1, "headingIndex": 0, "allowMerge": True, "clientSaveKey": "diagram-merge-1"}
    inserted = client.post(path, json=body, headers=headers)
    assert inserted.status_code == 200, inserted.text
    data = inserted.json()["data"]
    ref = f"docx-image:diagram-{gid}"
    assert data["merged"] is True and ref in data["markdownContent"] and "B 改引用段。" in data["markdownContent"]
    assert data["markdownContent"].index(ref) < data["markdownContent"].index("# 2 引用")
    assert [item["ref"] for item in data["formatTemplate"]["images"]].count(ref) == 1
    # 同键重放返回第一次结果；换键再插 → 已在文档中
    again = client.post(path, json=body, headers=headers)
    assert again.status_code == 200 and again.json()["data"]["revision"] == data["revision"]
    duplicate = client.post(path, json={**body, "clientSaveKey": "diagram-merge-2"}, headers=headers)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "DIAGRAM_ALREADY_INSERTED"


def test_restore_and_ai_undo_stay_strict(team):
    # 用例 18：B 修改后 A 用旧 expectedRevision 恢复 → 409 REVISION_CONFLICT（不合并）；撤回 AI 同理
    client, did, _ = team
    _other_edits(client, did, "第一段。", "B 改第一段。")
    headers = _as(client, "editor")
    restored = client.post(f"/api/v1/documents/{did}/versions/0/restore", json={"expectedRevision": 1}, headers=headers)
    assert restored.status_code == 409 and restored.json()["error"]["code"] == "REVISION_CONFLICT"
    undo = client.patch(f"/api/v1/documents/{did}/content", headers=headers,
                        json={"expectedRevision": 1, "markdownContent": TEXT, "retractMemoryRevision": 1})
    assert undo.status_code == 409 and undo.json()["error"]["code"] == "REVISION_CONFLICT"
    merged_undo = client.patch(f"/api/v1/documents/{did}/content", headers=headers,
                               json={"expectedRevision": 1, "markdownContent": TEXT, "retractMemoryRevision": 1, "allowMerge": True})
    assert merged_undo.status_code == 422
    assert "B 改第一段。" in current(client, did)["markdownContent"]
