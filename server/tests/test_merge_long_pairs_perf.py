"""代码审查 M6：长段落大范围修改（60 段 × 1000 字，每段都改）时逐对配对判定曾要 33s；现在超过总字数上限整块处理，保存远小于 15s。"""

import time

from app.document_merge import merge3
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

FILLER = "本条款规定了软件需求规格说明的编写要求，" * 50


def _doc(count: int = 60) -> str:
    return "\n\n".join(["前言段：说明。"] + [f"第{i + 1}段：{FILLER}术语甲 编号 {i + 1}。" for i in range(count)] + ["结尾段：完。"]) + "\n"


def test_merge3_long_paragraph_wide_edit_is_fast():
    base = _doc()
    theirs = base.replace("结尾段：完。", "结尾段：完（张改）。")
    mine = base.replace("术语甲", "术语乙")
    started = time.perf_counter()
    result = merge3(base, theirs, mine)
    assert time.perf_counter() - started < 3.0
    assert not result.conflicts and "术语甲" not in result.markdown and "完（张改）" in result.markdown


def test_save_long_paragraph_wide_edit_against_other_edit_is_fast(team):
    client, did, _ = team
    base = _doc(40)
    assert save(client, did, 1, base, _as(client, "editor")).status_code == 200
    rev = current(client, did)["revision"]
    assert save(client, did, rev, base.replace("结尾段：完。", "结尾段：完（张改）。"), _as(client, "editor")).status_code == 200
    started = time.perf_counter()
    response = save(client, did, rev, base.replace("术语甲", "术语乙"), _as(client, "editor2"))
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    assert elapsed < 8.0, f"保存耗时 {elapsed:.1f}s"
    text = current(client, did)["markdownContent"]
    assert "术语甲" not in text and "完（张改）" in text
