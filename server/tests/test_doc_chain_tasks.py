"""内置模板、依赖链与开发计划任务分解（只读）。"""

from app.doc_chain import CATALOG, chapter_progress, match_catalog
from app.plan_tasks import resolve_targets, section_tables
from tests.test_documents import document_context  # noqa: F401

PLAN = """# 1 范围

说明。

# 4 任务分解

| 任务 | 负责人 | 涉及章节 |
| --- | --- | --- |
| 需求分析 | 文档测试 | 软件需求规格说明 3.2、3.4；软件开发计划 4 任务分解 |
| 未知 | 某人 | 不存在的文档 9.9 |

# 5 公共框架分工

| 公共框架 | 负责人 |
| --- | --- |
| 软件运行环境 | 文档测试 |
"""


def test_catalog_is_acyclic_and_matches_names():
    keys = {item["key"] for item in CATALOG}
    for item in CATALOG:
        assert set(item["upstream"]) <= keys
    assert match_catalog("综合保障软件需求规格说明")["key"] == "srs"
    assert match_catalog("随便")is None


def test_progress_and_tables():
    written, total, _ = chapter_progress("# 1 范围\n\n（提示）\n\n# 2 目标\n\n正文。\n")
    assert (written, total) == (1, 2)
    rows = section_tables(PLAN, "任务分解")
    assert [r["任务"] for r in rows] == ["需求分析", "未知"]
    docs = [{"id": "a", "name": "软件需求规格说明", "catalogName": "软件需求规格说明",
             "headings": [{"title": "3.2 功能需求"}, {"title": "3.4 接口需求"}]}]
    targets = resolve_targets("软件需求规格说明 3.2、3.4；其他 1", docs)
    assert [t["chapter"] for t in targets[:2]] == ["3.2 功能需求", "3.4 接口需求"] and targets[2]["found"] is False


def test_seed_chain_and_plan_api(document_context):  # noqa: F811
    client, headers, project, *_ = document_context
    seeded = client.post("/api/v1/templates/builtin:seed", headers=headers)
    assert seeded.status_code == 200, seeded.text
    assert len(seeded.json()["data"]["created"]) == len(CATALOG)
    assert client.post("/api/v1/templates/builtin:seed", headers=headers).json()["data"]["created"] == []
    chain = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]
    assert all(not n["created"] and n["templateDocumentId"] for n in chain["nodes"])
    empty = client.get(f"/api/v1/projects/{project}/plan-tasks").json()["data"]
    assert empty["planDocument"] is None
    plan_td = next(n for n in chain["nodes"] if n["key"] == "dev-plan")["templateDocumentId"]
    srs_td = next(n for n in chain["nodes"] if n["key"] == "srs")["templateDocumentId"]
    for name, td in [("软件开发计划", plan_td), ("软件需求规格说明", srs_td)]:
        r = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                        json={"name": name, "sourceType": "template", "templateDocumentId": td})
        assert r.status_code == 200, r.text
    chain = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]
    assert {n["key"] for n in chain["nodes"] if n["created"]} == {"dev-plan", "srs"}
    plan = next(n for n in chain["nodes"] if n["key"] == "dev-plan")
    doc = client.get(f"/api/v1/documents/{plan['documentId']}").json()["data"]
    saved = client.patch(f"/api/v1/documents/{plan['documentId']}/content", headers=headers,
                         json={"expectedRevision": doc["revision"], "markdownContent": PLAN})
    assert saved.status_code == 200, saved.text
    data = client.get(f"/api/v1/projects/{project}/plan-tasks").json()["data"]
    task = data["tasks"][0]
    assert task["assignee"]["isMe"] and task["documentCount"] == 2
    assert all(t["found"] for t in task["targets"])
    runtime = next(f for f in data["frameworks"] if f["key"] == "runtime")
    assert runtime["assignee"]["name"] == "文档测试" and len(runtime["references"]) == 2


# ---------------------------------------------------------------- 第二轮：上游复制、任务写回 A07、公共框架

def _seed_project(context):
    client, headers, project, *_ = context
    assert client.post("/api/v1/templates/builtin:seed", headers=headers).status_code == 200
    chain = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]
    return {n["key"]: n["templateDocumentId"] for n in chain["nodes"]}


def _create(client, headers, project, name, td, **extra):
    r = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                    json={"name": name, "sourceType": "template", "templateDocumentId": td, **extra})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _write(client, headers, did, text):
    doc = client.get(f"/api/v1/documents/{did}").json()["data"]
    r = client.patch(f"/api/v1/documents/{did}/content", headers=headers, json={"expectedRevision": doc["revision"], "markdownContent": text})
    assert r.status_code == 200, r.text


def test_template_upstream_edit_and_cycle(document_context):  # noqa: F811
    client, headers, *_ = document_context
    tds = _seed_project(document_context)
    got = client.get(f"/api/v1/template-documents/{tds['srs']}/upstreams").json()["data"]
    assert [u["id"] for u in got["upstreams"]] == [tds["task-book"]]
    bad = client.put(f"/api/v1/template-documents/{tds['task-book']}/upstreams", headers=headers, json={"upstreamIds": [tds["srs"]]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "UPSTREAM_CYCLE"
    ok = client.put(f"/api/v1/template-documents/{tds['user-manual']}/upstreams", headers=headers, json={"upstreamIds": [tds["srs"], tds["design"]]})
    assert ok.status_code == 200 and len(ok.json()["data"]["upstreams"]) == 2


def test_create_copies_upstream_chapters_and_links(document_context):  # noqa: F811
    client, headers, project, *_ = document_context
    tds = _seed_project(document_context)
    book = _create(client, headers, project, "软件任务计划书", tds["task-book"])
    _write(client, headers, book["id"], "# 1 范围\n\n## 1.1 标识\n\n本系统标识为 ZB-2。\n\n# 3 任务目标\n\n目标正文。\n")
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    text = client.get(f"/api/v1/documents/{srs['id']}").json()["data"]["markdownContent"]
    assert "本系统标识为 ZB-2。" in text and "来源：《软件任务计划书》" in text
    assert "目标正文" not in text  # 没有对应章节的不复制
    ups = client.get(f"/api/v1/documents/{srs['id']}/upstreams").json()["data"]["upstreams"]
    assert ups[0]["documentId"] == book["id"] and ups[0]["copiedChapters"][0]["chapter"].endswith("标识")
    _write(client, headers, book["id"], "# 1 范围\n\n## 1.1 标识\n\n改了。\n")
    after = client.get(f"/api/v1/documents/{srs['id']}").json()["data"]["markdownContent"]
    assert "改了" not in after  # 不同步
    assert client.get(f"/api/v1/documents/{srs['id']}/upstreams").json()["data"]["upstreams"][0]["changedSinceLink"]
    chain = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]
    assert next(n for n in chain["nodes"] if n["key"] == "srs")["upstreamChanged"]
    # 手动清空上游（Q-1）、非法上游
    assert client.put(f"/api/v1/documents/{srs['id']}/upstreams", headers=headers, json={"upstreamDocumentIds": []}).json()["data"]["upstreams"] == []
    assert client.put(f"/api/v1/documents/{book['id']}/upstreams", headers=headers, json={"upstreamDocumentIds": [book["id"]]}).status_code == 422


def test_tasks_write_back_and_public_blocks(document_context):  # noqa: F811
    from app.db import connect
    client, headers, project, *_ = document_context
    tds = _seed_project(document_context)
    me = client.get("/api/v1/auth/me").json()["data"]["user"]["id"]
    with connect() as conn:
        conn.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (%s,%s,'owner')", [str(project), me])
        conn.commit()
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    plan = _create(client, headers, project, "软件开发计划", tds["dev-plan"])
    chapters = client.get(f"/api/v1/projects/{project}/documents/{srs['id']}/chapters").json()["data"]["chapters"]
    func = next(c for c in chapters if c["title"].endswith("功能需求"))
    pc = client.get(f"/api/v1/projects/{project}/documents/{plan['id']}/chapters").json()["data"]["chapters"]
    split = next(c for c in pc if c["title"].endswith("任务分解"))
    r = client.post(f"/api/v1/projects/{project}/tasks", headers=headers, json={"title": "需求分析", "assigneeId": me, "targets": [
        {"documentId": srs["id"], "objectId": func["objectId"]}, {"documentId": plan["id"], "objectId": split["objectId"]}]})
    assert r.status_code == 201, r.text
    task = r.json()["data"]
    assert task["documentCount"] == 2 and all(t["owners"] for t in task["targets"])
    a07 = client.get(f"/api/v1/documents/{srs['id']}/assignments").json()["data"]
    assert any(e["objectId"] == func["objectId"] for e in a07["explicit"])
    r = client.patch(f"/api/v1/tasks/{task['id']}", headers=headers, json={"expectedRevision": 0, "targets": [
        {"documentId": plan["id"], "objectId": split["objectId"]}]})
    assert r.status_code == 200, r.text
    a07 = client.get(f"/api/v1/documents/{srs['id']}/assignments").json()["data"]
    assert not any(e["objectId"] == func["objectId"] for e in a07["explicit"])
    assert client.patch(f"/api/v1/tasks/{task['id']}", headers=headers, json={"expectedRevision": 0}).status_code == 409
    md = client.get(f"/api/v1/projects/{project}/tasks:markdown").json()["data"]["markdown"]
    assert "需求分析" in md
    assert client.delete(f"/api/v1/tasks/{task['id']}?expectedRevision=1", headers=headers).status_code == 204
    assert client.get(f"/api/v1/documents/{plan['id']}/assignments").json()["data"]["explicit"] == []
    data = client.get(f"/api/v1/projects/{project}/tasks").json()["data"]
    runtime = next(f for f in data["frameworks"] if f["key"] == "runtime")
    assert len(runtime["references"]) == 2 and runtime["canEdit"]
    refs = client.get(f"/api/v1/documents/{srs['id']}/public-refs").json()["data"]["references"]
    assert refs and not any(r["updated"] for r in refs)
    saved = client.put(f"/api/v1/public-blocks/{runtime['id']}", headers=headers, json={"expectedRevision": 0, "body": "Windows 10"})
    assert saved.status_code == 200, saved.text
    ref = next(r for r in client.get(f"/api/v1/documents/{srs['id']}/public-refs").json()["data"]["references"] if r["block"]["key"] == "runtime")
    assert ref["updated"] and ref["block"]["body"] == "Windows 10"
    assert client.post(f"/api/v1/documents/{srs['id']}/public-refs/{runtime['id']}:seen", headers=headers).status_code == 200
    ref = next(r for r in client.get(f"/api/v1/documents/{srs['id']}/public-refs").json()["data"]["references"] if r["block"]["key"] == "runtime")
    assert not ref["updated"]
