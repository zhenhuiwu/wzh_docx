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


def _srs_document(client, headers, project, markdown=None):
    stored = store_bytes(name=SRS.name, media_type=DOCX, purpose="attachment", project_id=project, content=SRS.read_bytes())
    created = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                          json={"name": "SRS", "sourceType": "upload", "wordFileId": str(stored.id)})
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
