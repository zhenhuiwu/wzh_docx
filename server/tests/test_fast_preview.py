"""43 快速预览：/preview/blocks 按对象给 HTML 片段；/objects 标出 Word 正文样式标题，与整篇预览的 data-word-heading 一致。"""

from __future__ import annotations

import time
from pathlib import Path

from lxml import html as html_dom

from app import fast_preview, preview_cache
from app.db import connect
from app.docx_parser import parse_docx
from app.files import store_bytes
from tests.test_documents import document_context  # noqa: F401  (fixture reuse)
from tests.test_preview_large import big_markdown

SRS = next((Path(__file__).resolve().parents[2] / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _srs_document(client, headers, project, markdown=None, name="SRS"):
    stored = store_bytes(name=SRS.name, media_type=DOCX, purpose="attachment", project_id=project, content=SRS.read_bytes())
    created = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                          json={"name": name, "sourceType": "upload", "wordFileId": str(stored.id)})
    assert created.status_code == 200, created.text
    did = created.json()["data"]["id"]
    if markdown is not None:
        with connect() as connection:
            connection.execute("UPDATE documents SET markdown_content=%s WHERE id=%s", (markdown, did))
            connection.commit()
    return did


def test_word_heading_flags_match_full_preview(document_context):  # noqa: F811
    client, headers, project, *_ = document_context
    fast_preview.clear()
    did = _srs_document(client, headers, project)
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    flagged = {item["id"]: item["wordHeadingLevel"] for item in objects if item.get("isWordHeading")}
    assert flagged and all(item["kind"] == "paragraph" for item in objects if item["id"] in flagged)
    full = client.get(f"/api/v1/documents/{did}/preview").json()["data"]
    root = html_dom.fragment_fromstring(full["html"], create_parent="div")
    expected = {node.get("data-object-id"): int(node.get("data-word-heading"))
                for node in root.xpath(".//*[@data-word-heading][@data-object-id]")}
    assert flagged == expected
    identity = next(item for item in objects if item["markdown"].strip() == "标识")
    assert flagged[identity["id"]] == 2


def test_preview_blocks_pages_and_carries_ids(document_context):  # noqa: F811
    client, headers, project, *_ = document_context
    did = _srs_document(client, headers, project)
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    first = client.get(f"/api/v1/documents/{did}/preview/blocks?offset=0&limit=10").json()["data"]
    assert first["total"] == len(objects) and len(first["blocks"]) == 10 and first["nextOffset"] == 10
    rest = client.get(f"/api/v1/documents/{did}/preview/blocks?offset=10&limit=5000").json()["data"]
    assert rest["nextOffset"] is None
    blocks = first["blocks"] + rest["blocks"]
    assert [item["objectId"] for item in blocks] == [item["id"] for item in objects]
    by_id = {item["id"]: item for item in objects}
    for block in blocks:
        assert block["revision"] == by_id[block["objectId"]]["revision"]
        assert block.get("wordHeadingLevel") == by_id[block["objectId"]].get("wordHeadingLevel")
        if block["kind"] == "heading":
            assert block["html"].startswith(f"<h{block['headingLevel']}")
    assert client.get(f"/api/v1/documents/{did}/preview/blocks?limit=0").status_code == 422


def test_large_document_fast_preview_is_cheap_cold(document_context, capsys):  # noqa: F811
    client, headers, project, *_ = document_context
    fast_preview.clear()
    preview_cache.clear()
    did = _srs_document(client, headers, project, big_markdown(parse_docx(str(SRS)).markdown))
    started = time.perf_counter()
    objects = client.get(f"/api/v1/documents/{did}/objects")
    objects_time = time.perf_counter() - started
    started = time.perf_counter()
    blocks = client.get(f"/api/v1/documents/{did}/preview/blocks?limit=5000")
    blocks_time = time.perf_counter() - started
    assert objects.status_code == 200 and blocks.status_code == 200
    assert blocks.json()["data"]["total"] == len(objects.json()["data"]["objects"]) > 2000
    with capsys.disabled():
        print(f"\n[43] objects(cold)={objects_time:.2f}s preview/blocks(cold)={blocks_time:.2f}s")
    assert objects_time < 5 and blocks_time < 5  # 共享机器上留余量；本机实测见 43 号说明


def test_patch_warms_fast_preview_for_new_revision(document_context, monkeypatch):  # noqa: F811
    """关页保存提速：块级保存提交后后台预热新修订的快速预览，重开时 /preview/blocks 直接命中缓存。"""
    client, headers, project, *_ = document_context
    fast_preview.clear()
    did = _srs_document(client, headers, project)
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    revision = client.get(f"/api/v1/documents/{did}").json()["data"]["revision"]
    target = [item for item in objects if item["kind"] == "paragraph" and not item.get("isWordHeading")
              and len(item["markdown"]) > 40 and not any(mark in item["markdown"] for mark in "[]!<|")][-1]
    threads = []
    original = fast_preview.warm_after_commit
    monkeypatch.setattr(fast_preview, "warm_after_commit", lambda *a: threads.append(original(*a)) or threads[-1])
    saved = client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers, json={
        "clientSaveKey": "warm-after-patch-1", "baseDocumentRevision": revision, "atomic": True,
        "ops": [{"op": "update", "objectId": target["id"], "baseRevision": target["revision"],
                 "markdown": target["markdown"] + " 已改"}]})
    assert saved.status_code == 200, saved.text
    new_revision = saved.json()["data"]["documentRevision"]
    assert threads and threads[0] is not None
    threads[0].join(10)
    computed = []
    real = fast_preview._compute
    monkeypatch.setattr(fast_preview, "_compute", lambda *a: computed.append(a) or real(*a))
    page = client.get(f"/api/v1/documents/{did}/preview/blocks?limit=5000").json()["data"]
    assert page["documentRevision"] == new_revision
    assert computed == []  # 命中预热结果
    block = next(item for item in page["blocks"] if item["objectId"] == target["id"])
    assert "已改" in block["html"] and block["revision"] == target["revision"] + 1


def test_reopen_reads_wait_for_inflight_patch(document_context, monkeypatch):  # noqa: F811
    """关页保存优先：块级保存进行中时，重开的 /objects 与 /preview/blocks 等它落库，直接读到保存后的正文。"""
    import threading

    from app import documents
    client, headers, project, *_ = document_context
    fast_preview.clear()
    did = _srs_document(client, headers, project)
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    revision = client.get(f"/api/v1/documents/{did}").json()["data"]["revision"]
    target = [item for item in objects if item["kind"] == "paragraph" and not item.get("isWordHeading")
              and len(item["markdown"]) > 40 and not any(mark in item["markdown"] for mark in "[]!<|")][-1]
    checking, release = threading.Event(), threading.Event()
    real_check = documents._check_word

    def slow_check(*args, **kwargs):
        checking.set()
        release.wait(5)
        return real_check(*args, **kwargs)
    monkeypatch.setattr(documents, "_check_word", slow_check)
    result = {}

    def save():
        result["patch"] = client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers, json={
            "clientSaveKey": "inflight-patch-1", "baseDocumentRevision": revision, "atomic": True,
            "ops": [{"op": "update", "objectId": target["id"], "baseRevision": target["revision"],
                     "markdown": target["markdown"] + " 关页"}]})
    saver = threading.Thread(target=save)
    saver.start()
    assert checking.wait(5)
    threading.Timer(0.3, release.set).start()
    detail = client.get(f"/api/v1/documents/{did}").json()["data"]
    reopened = client.get(f"/api/v1/documents/{did}/objects").json()["data"]
    page = client.get(f"/api/v1/documents/{did}/preview/blocks?limit=5000").json()["data"]
    saver.join(10)
    assert result["patch"].status_code == 200, result["patch"].text
    assert detail["revision"] == reopened["documentRevision"] == page["documentRevision"] == revision + 1
    assert "关页" in detail["markdownContent"]
    assert any(item["markdown"].endswith("关页") for item in reopened["objects"])


def test_word_headings_parsed_once_off_process(monkeypatch):
    """N1：同一原稿的标题表在快速进程池里只解析一次；/objects 与 /preview/blocks 同时到达时另一个等结果。"""
    import threading

    calls = []
    gate = threading.Event()

    def fake_call(fn, *args):
        calls.append((fn, args))
        gate.wait(5)
        return fn(*args)
    monkeypatch.setattr(preview_cache, "fast_call", fake_call)
    fast_preview.clear()
    results = []
    threads = [threading.Thread(target=lambda: results.append(fast_preview.word_heading_texts("srs-key", SRS))) for _ in range(3)]
    [thread.start() for thread in threads]
    time.sleep(0.2)
    gate.set()
    [thread.join(10) for thread in threads]
    assert len(calls) == 1 and calls[0][0] is fast_preview.parse_headings
    assert len(results) == 3 and results[0] and all(item == results[0] for item in results)


def test_fast_pool_runs_heading_parse():
    """快速进程池真的在子进程里算出与本进程相同的结果。"""
    assert preview_cache.fast_call(fast_preview.parse_headings, str(SRS)) == fast_preview.parse_headings(str(SRS))


def test_large_content_save_returns_object_revisions(document_context):  # noqa: F811
    """QA 复验 12：大文档整篇保存的响应带存活对象 [id, revision]（按位置），与 /objects 一致；小文档不带。"""
    client, headers, project, *_ = document_context
    did = _srs_document(client, headers, project, big_markdown(parse_docx(str(SRS)).markdown))
    current = client.get(f"/api/v1/documents/{did}").json()["data"]
    edited = current["markdownContent"].replace("。", "。改", 1)
    saved = client.patch(f"/api/v1/documents/{did}/content", headers=headers, json={
        "expectedRevision": current["revision"], "markdownContent": edited, "allowMerge": True, "clientSaveKey": "objrev-save-1"})
    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    assert data["objectRevisions"] == [[item["id"], item["revision"]] for item in objects]
    small = _srs_document(client, headers, project, name="SRS-small")
    detail = client.get(f"/api/v1/documents/{small}").json()["data"]
    target = detail["markdownContent"].rstrip("\n") + "\n\n补一段。\n"
    plain = client.patch(f"/api/v1/documents/{small}/content", headers=headers, json={
        "expectedRevision": detail["revision"], "markdownContent": target, "allowMerge": True, "clientSaveKey": "objrev-save-2"})
    assert plain.status_code == 200, plain.text
    assert "objectRevisions" not in plain.json()["data"]


def test_content_save_warms_fast_preview(document_context, monkeypatch):  # noqa: F811
    """整篇保存（/content）后同样预热新修订的快速预览：重开时 /preview/blocks 不现算。"""
    client, headers, project, *_ = document_context
    fast_preview.clear()
    did = _srs_document(client, headers, project, name="SRS-warm-content")
    threads = []
    original = fast_preview.warm_after_commit
    monkeypatch.setattr(fast_preview, "warm_after_commit", lambda *a: threads.append(original(*a)) or threads[-1])
    detail = client.get(f"/api/v1/documents/{did}").json()["data"]
    saved = client.patch(f"/api/v1/documents/{did}/content", headers=headers, json={
        "expectedRevision": detail["revision"], "markdownContent": detail["markdownContent"].rstrip("\n") + "\n\n补一段。\n",
        "allowMerge": True, "clientSaveKey": "warm-content-1"})
    assert saved.status_code == 200, saved.text
    assert threads and threads[0] is not None
    threads[0].join(10)
    computed = []
    real = fast_preview._compute
    monkeypatch.setattr(fast_preview, "_compute", lambda *a: computed.append(a) or real(*a))
    page = client.get(f"/api/v1/documents/{did}/preview/blocks?limit=5000").json()["data"]
    assert page["documentRevision"] == saved.json()["data"]["revision"] and computed == []


def test_close_patch_waits_for_inflight_whole_save(document_context, monkeypatch):  # noqa: F811
    """QA 复验 13：Ctrl+S 的整篇保存还在检查时关页的块级保存到达——先等整篇保存落库，内容相同就直接返回，
    不再做第二遍 Word 写回检查；重开读到的就是落库后的正文。"""
    import threading

    from app import documents
    client, headers, project, *_ = document_context
    did = _srs_document(client, headers, project, name="SRS-ctrl-s-close")
    detail = client.get(f"/api/v1/documents/{did}").json()["data"]
    objects = client.get(f"/api/v1/documents/{did}/objects").json()["data"]["objects"]
    target = [item for item in objects if item["kind"] == "paragraph" and not item.get("isWordHeading")
              and len(item["markdown"]) > 40 and not any(mark in item["markdown"] for mark in "[]!<|")][-1]
    edited_block = target["markdown"] + " 速改"
    edited = detail["markdownContent"].replace(target["markdown"], edited_block, 1)
    checks, entered, release = [], threading.Event(), threading.Event()
    real_check = documents._check_word

    def slow_check(*args, **kwargs):
        checks.append(1)
        if len(checks) == 1:
            entered.set()
            release.wait(5)
        return real_check(*args, **kwargs)
    monkeypatch.setattr(documents, "_check_word", slow_check)
    out = {}

    def whole():
        out["content"] = client.patch(f"/api/v1/documents/{did}/content", headers=headers, json={
            "expectedRevision": detail["revision"], "markdownContent": edited, "allowMerge": True, "clientSaveKey": "ctrl-s-1"})

    def close():
        out["patch"] = client.post(f"/api/v1/documents/{did}/objects/patch", headers=headers, json={
            "clientSaveKey": "close-after-ctrl-s", "baseDocumentRevision": detail["revision"], "atomic": True,
            "ops": [{"op": "update", "objectId": target["id"], "baseRevision": target["revision"], "markdown": edited_block}]})
    saver = threading.Thread(target=whole)
    saver.start()
    assert entered.wait(5)
    closer = threading.Thread(target=close)
    closer.start()
    threading.Timer(0.3, release.set).start()
    saver.join(15)
    closer.join(15)
    assert out["content"].status_code == 200, out["content"].text
    assert out["patch"].status_code == 200, out["patch"].text
    assert len(checks) == 1  # 只检查了一次（块级保存没有重复检查）
    assert out["patch"].json()["data"]["documentRevision"] == detail["revision"] + 1
    final = client.get(f"/api/v1/documents/{did}").json()["data"]
    assert final["revision"] == detail["revision"] + 1 and "速改" in final["markdownContent"]
