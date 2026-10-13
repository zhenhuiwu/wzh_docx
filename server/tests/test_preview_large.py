"""P0-2：大文档（约 370 节、19 万字、2000 段）打开不被整篇预览拖住；预览有缓存。"""

from __future__ import annotations

import random
import time
from pathlib import Path

from app import preview_cache
from app.db import connect
from app.docx_parser import parse_docx
from app.files import store_bytes
from tests.test_documents import document_context  # noqa: F401  (fixture reuse)

ROOT = Path(__file__).resolve().parents[2]


def big_markdown(base: str) -> str:
    rnd = random.Random(1)
    parts = [base]
    for chapter in range(1, 61):
        parts.append(f"# {chapter} 扩展章节{chapter}")
        for section in range(1, 6):
            parts.append(f"## {chapter}.{section} 小节{section}")
            for index in range(5):
                parts.append(f"第{chapter}.{section}.{index}段：" + "".join(rnd.choice("系统应支持用户管理数据查询导出功能性能安全") for _ in range(105)) + "。")
    return "\n\n".join(parts)


def test_large_document_opens_fast_and_preview_is_cached(document_context, capsys):  # noqa: F811
    client, headers, project, _other, _stored, _source = document_context
    source = next((ROOT / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
    stored = store_bytes(name=source.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         purpose="attachment", project_id=project, content=source.read_bytes())
    created = client.post(f"/api/v1/projects/{project}/documents", headers=headers,
                          json={"name": "大文档", "sourceType": "upload", "wordFileId": str(stored.id)})
    assert created.status_code == 200, created.text
    did = created.json()["data"]["id"]
    markdown = big_markdown(parse_docx(str(source)).markdown)
    with connect() as connection:
        connection.execute("UPDATE documents SET markdown_content=%s WHERE id=%s", (markdown, did))
        connection.commit()
    preview_cache.clear()

    started = time.perf_counter()
    detail = client.get(f"/api/v1/documents/{did}")
    opened = time.perf_counter() - started
    assert detail.status_code == 200 and detail.json()["data"]["markdownContent"] == markdown
    assert opened < 1.0, f"正文接口 {opened:.2f}s，不应等整篇预览"

    started = time.perf_counter()
    first = client.get(f"/api/v1/documents/{did}/preview")
    cold = time.perf_counter() - started
    assert first.status_code == 200, first.text
    started = time.perf_counter()
    second = client.get(f"/api/v1/documents/{did}/preview")
    warm = time.perf_counter() - started
    assert second.json()["data"]["html"] == first.json()["data"]["html"]
    with capsys.disabled():
        print(f"\n[P0-2] chars={len(markdown)} open={opened:.2f}s preview(first)={cold:.2f}s preview(cached)={warm:.2f}s")
    assert cold < 15.0 and warm < 3.0
