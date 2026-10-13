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
    assert saved.status_code == 409 and saved.json()["error"]["code"] == "CHAPTER_READONLY"  # 任务分解只读（40 P2-5）
    from app.db import connect  # 旧版手写任务表（迁移前的数据）：只读解析器仍能读
    with connect() as conn:
        conn.execute("UPDATE documents SET markdown_content=%s WHERE id=%s", [PLAN, plan["documentId"]])
        conn.commit()
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
    _write(client, headers, book["id"], "# 1 范围\n\n（在此编写本章内容。）\n\n## 1.1 标识\n\n本系统标识为 ZB-2。\n\n（提示：写明版本。）\n\n# 3 任务目标\n\n目标正文。\n")
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    text = client.get(f"/api/v1/documents/{srs['id']}").json()["data"]["markdownContent"]
    assert "本系统标识为 ZB-2。" in text and "来源：《软件任务计划书》" in text
    assert "目标正文" not in text  # 没有对应章节的不复制
    # 40 P2-3：上游“1 范围”只有提示语，不当作初稿复制；复制时也不带上游的提示语
    assert text.count("来源：《软件任务计划书》") == 1 and "范围，创建时复制" not in text and "写明版本" not in text
    ups = client.get(f"/api/v1/documents/{srs['id']}/upstreams").json()["data"]["upstreams"]
    assert ups[0]["documentId"] == book["id"] and ups[0]["copiedChapters"][0]["chapter"].endswith("标识")
    _write(client, headers, book["id"], "# 1 范围\n\n## 1.1 标识\n\n改了。\n")
    after = client.get(f"/api/v1/documents/{srs['id']}").json()["data"]["markdownContent"]
    assert "改了" not in after  # 不同步
    assert client.get(f"/api/v1/documents/{srs['id']}/upstreams").json()["data"]["upstreams"][0]["changedSinceLink"]
    chain = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]
    assert not next(n for n in chain["nodes"] if n["key"] == "srs")["upstreamChanged"]  # 只改正文、未进新基线不提示
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
    assert task["writtenCount"] == sum(1 for t in task["targets"] if t["written"])
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
    assert ref["canAck"] is True
    assert ref["updated"] and ref["block"]["body"] == "Windows 10"
    assert client.post(f"/api/v1/documents/{srs['id']}/public-refs/{runtime['id']}:seen", headers=headers).status_code == 200
    ref = next(r for r in client.get(f"/api/v1/documents/{srs['id']}/public-refs").json()["data"]["references"] if r["block"]["key"] == "runtime")
    assert not ref["updated"]


def test_upstream_new_baseline_is_baseline_based(document_context):  # noqa: F811
    from app.db import connect
    client, headers, project, *_ = document_context
    client.post("/api/v1/templates/builtin:seed", headers=headers)
    nodes = client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]["nodes"]
    td = {n["key"]: n["templateDocumentId"] for n in nodes}
    srs = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                      json={"name": "软件需求规格说明", "sourceType": "template", "templateDocumentId": td["srs"]}).json()["data"]["id"]
    plan = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                       json={"name": "软件开发计划", "sourceType": "template", "templateDocumentId": td["dev-plan"],
                             "upstreamDocumentIds": [srs]}).json()["data"]["id"]

    def node(did):
        return next(n for n in client.get(f"/api/v1/projects/{project}/doc-chain").json()["data"]["nodes"] if n["documentId"] == did)

    def edit(did):  # 正文改动：只要 revision 变化，基线就会生成新文档版本
        with connect() as conn:
            conn.execute("UPDATE documents SET revision=revision+1, markdown_content=markdown_content || '\n\n新增。\n' WHERE id=%s", [did])
            conn.commit()

    edit(srs)  # 未进基线前的修改不算“上游有新基线”
    assert node(plan)["upstreamChanged"] is False and node(plan)["baselineSeq"] is None
    r = client.post(f"/api/v1/projects/{project}/baselines", headers=headers, json={"name": "评审", "documentIds": [srs, plan]})
    assert r.status_code == 201, r.text
    assert node(plan)["baselineSeq"] == 1 and node(plan)["upstreamChanged"] is False
    client.post(f"/api/v1/projects/{project}/baselines", headers=headers, json={"name": "无变化", "documentIds": [srs]})
    assert node(plan)["upstreamChanged"] is False  # 新基线但上游版本没变
    edit(srs)
    client.post(f"/api/v1/projects/{project}/baselines", headers=headers, json={"name": "需求更新", "documentIds": [srs]})
    up = node(plan)["upstreamBaselines"][0]
    assert node(plan)["upstreamChanged"] is True and (up["baseBaselineSeq"], up["latestBaselineSeq"]) == (1, 3)
    assert up["fromVersionSeq"] != up["toVersionSeq"]
    notices = client.get(f"/api/v1/documents/{plan}/upstream-notices", headers=headers).json()["data"]
    assert [n["documentId"] for n in notices["notices"]] == [srs] and notices["canAck"] is True
    acked = client.post(f"/api/v1/documents/{plan}/upstreams/{srs}:ack", headers=headers)
    assert acked.status_code == 200, acked.text and acked.json()["data"]["newBaseline"] is False
    assert node(plan)["upstreamChanged"] is False
    assert client.get(f"/api/v1/documents/{plan}/upstream-notices", headers=headers).json()["data"]["notices"] == []
    assert client.post(f"/api/v1/documents/{srs}/upstreams/{plan}:ack", headers=headers).status_code == 404


def test_shared_chapter_keeps_other_task_assignment(document_context):  # noqa: F811
    """40 P2-2：同一章节挂在两个任务上，删除/移出其中一个不清另一个任务的分工。"""
    from app.db import connect
    from app.identity import create_user
    client, headers, project, *_ = document_context
    tds = _seed_project(document_context)
    me = client.get("/api/v1/auth/me").json()["data"]["user"]["id"]
    sun = str(create_user(account="task-sun", name="孙", password="task-sun-password-1", roles=["project_creator"]))
    with connect() as conn:
        for uid in (me, sun):
            conn.execute("INSERT INTO project_members(project_id,user_id,role) VALUES (%s,%s,%s)", [str(project), uid, "owner" if uid == me else "editor"])
        conn.commit()
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    chapters = client.get(f"/api/v1/projects/{project}/documents/{srs['id']}/chapters").json()["data"]["chapters"]
    func = next(c for c in chapters if c["title"].endswith("功能需求"))["objectId"]
    iface = next(c for c in chapters if c["title"].endswith("接口需求"))["objectId"]

    def holders(oid):
        with connect() as conn:
            return {str(r["assignee_id"]) for r in conn.execute(
                "SELECT assignee_id FROM document_object_assignments WHERE document_id=%s AND object_id=%s", [srs["id"], oid]).fetchall()}

    def task(title, uid, oids):
        r = client.post(f"/api/v1/projects/{project}/tasks", headers=headers, json={"title": title, "assigneeId": uid,
                        "targets": [{"documentId": srs["id"], "objectId": o} for o in oids]})
        assert r.status_code == 201, r.text
        return r.json()["data"]

    c = task("C", me, [func])
    d = task("D", sun, [func, iface])
    assert holders(func) == {me, sun}  # 后建的任务不覆盖前一个任务的负责人
    r = client.patch(f"/api/v1/tasks/{d['id']}", headers=headers, json={"expectedRevision": 0, "targets": [{"documentId": srs["id"], "objectId": iface}]})
    assert r.status_code == 200, r.text
    assert holders(func) == {me} and holders(iface) == {sun}
    e = task("E", me, [func])  # 同一负责人两个任务
    assert client.delete(f"/api/v1/tasks/{e['id']}?expectedRevision=0", headers=headers).status_code == 204
    assert holders(func) == {me}  # C 仍为 me 认领
    assert client.delete(f"/api/v1/tasks/{d['id']}?expectedRevision=1", headers=headers).status_code == 204
    assert holders(iface) == set() and holders(func) == {me}
    assert client.delete(f"/api/v1/tasks/{c['id']}?expectedRevision=0", headers=headers).status_code == 204
    assert holders(func) == set()


def test_framework_and_task_split_chapters_are_readonly(document_context):  # noqa: F811
    """40 P2-5：引用处的公共框架章节、开发计划书“任务分解”只读；任务不能指向公共框架章节；任务分解由任务生成。"""
    client, headers, project, *_ = document_context
    tds = _seed_project(document_context)
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    plan = _create(client, headers, project, "软件开发计划", tds["dev-plan"])

    def doc(did):
        return client.get(f"/api/v1/documents/{did}").json()["data"]

    def save(did, md):
        return client.patch(f"/api/v1/documents/{did}/content", headers=headers,
                            json={"expectedRevision": doc(did)["revision"], "markdownContent": md})

    md = doc(srs["id"])["markdownContent"]
    head = next(line for line in md.splitlines() if line.lstrip("#").strip().endswith("运行环境"))
    r = save(srs["id"], md.replace(head, head + "\n\n私自改写的运行环境。", 1))
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_READONLY", r.text
    func = next(line for line in md.splitlines() if line.lstrip("#").strip().endswith("功能需求"))
    assert save(srs["id"], md.replace(func, func + "\n\n普通章节可以写。", 1)).status_code == 200

    chapters = client.get(f"/api/v1/projects/{project}/documents/{srs['id']}/chapters").json()["data"]["chapters"]
    runtime = next(c for c in chapters if c["title"].endswith("运行环境"))
    funcc = next(c for c in chapters if c["title"].endswith("功能需求"))
    r = client.post(f"/api/v1/projects/{project}/tasks", headers=headers, json={"title": "环境", "targets": [
        {"documentId": srs["id"], "objectId": runtime["objectId"]}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "FRAMEWORK_CHAPTER", r.text

    r = client.post(f"/api/v1/projects/{project}/tasks", headers=headers, json={"title": "需求细化", "targets": [
        {"documentId": srs["id"], "objectId": funcc["objectId"]}]})
    assert r.status_code == 201, r.text
    pmd = doc(plan["id"])["markdownContent"]
    assert "| 需求细化 | 未分配 |" in pmd and "功能需求" in pmd.split("任务分解", 1)[1]
    split_head = next(line for line in pmd.splitlines() if line.lstrip("#").strip().endswith("任务分解"))
    r2 = save(plan["id"], pmd.replace(split_head, split_head + "\n\n手写任务。", 1))
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "CHAPTER_READONLY"
    task = r.json()["data"]
    assert client.delete(f"/api/v1/tasks/{task['id']}?expectedRevision=0", headers=headers).status_code == 204
    assert "需求细化" not in doc(plan["id"])["markdownContent"] and "尚无任务" in doc(plan["id"])["markdownContent"]


def test_task_split_regeneration_skips_unchanged(document_context):  # noqa: F811
    """任务变化但生成的“任务分解”内容不变时不保存、不新增修订。"""
    client, headers, project, *_ = document_context
    tds = _seed_project(document_context)
    srs = _create(client, headers, project, "软件需求规格说明", tds["srs"])
    plan = _create(client, headers, project, "软件开发计划", tds["dev-plan"])
    rev = lambda: client.get(f"/api/v1/documents/{plan['id']}").json()["data"]["revision"]  # noqa: E731
    chapters = client.get(f"/api/v1/projects/{project}/documents/{srs['id']}/chapters").json()["data"]["chapters"]
    func = next(c for c in chapters if c["title"].endswith("功能需求"))
    task = client.post(f"/api/v1/projects/{project}/tasks", headers=headers, json={"title": "需求细化", "targets": [
        {"documentId": srs["id"], "objectId": func["objectId"]}]}).json()["data"]
    after_create = rev()
    r = client.patch(f"/api/v1/tasks/{task['id']}", headers=headers, json={"expectedRevision": 0, "description": "只改说明"})
    assert r.status_code == 200, r.text
    assert rev() == after_create  # 表格不含说明：内容不变，不保存
    r = client.patch(f"/api/v1/tasks/{task['id']}", headers=headers, json={"expectedRevision": 1, "title": "需求细化二"})
    assert r.status_code == 200 and rev() == after_create + 1
    md = client.get(f"/api/v1/documents/{plan['id']}").json()["data"]["markdownContent"]
    lines = md.splitlines()
    assert not any(a.startswith("|") and b.startswith("#") for a, b in zip(lines, lines[1:]))  # 下一个标题没被吞进表格
