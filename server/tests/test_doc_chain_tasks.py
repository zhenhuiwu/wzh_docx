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
