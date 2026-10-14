"""关页保存优先（复验 P2-1 后续）：块级保存进行中时，同一文档的重开读请求（/objects、/preview/blocks、/preview）稍等它落库。

实测（360 节大文档，关页后立即重开）：保存本身约 1.1 s 是 Word 可写回检查（纯 CPU），新标签的读请求与它抢 GIL，
保存被拖到 1.5–2.3 s；而这些读请求读到的是落库前的旧正文，前端核对到保存落库后还要再读一遍。
让读请求先等保存（最多 WAIT_S 秒），保存独占 CPU 更快落库，读请求直接拿到落库后的正文，也不白算旧修订。
单进程内有效（与 document_lock 相同）；等不到就照常读，不会因此报错。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

WAIT_S = 3.0
_cond = threading.Condition()
_active: dict[str, int] = {}
_content: dict[str, int] = {}


def _dec(table: dict[str, int], key: str) -> None:
    left = table[key] - 1
    if left:
        table[key] = left
    else:
        del table[key]


@contextmanager
def saving(document_id, *, whole: bool = False):
    """whole=True：整篇保存（/content）只登记、不让读请求等（编辑中的自动保存不该拖慢别人打开）；
    块级保存会先等同一文档进行中的整篇保存（见 wait_whole_saves），它等待期间读请求照常等它。"""
    key = str(document_id)
    table = _content if whole else _active
    with _cond:
        table[key] = table.get(key, 0) + 1
    try:
        yield
    finally:
        with _cond:
            _dec(table, key)
            _cond.notify_all()


def wait_whole_saves(document_id, timeout: float = WAIT_S) -> bool:
    """QA 复验 13：Ctrl+S 后 0.3 s 内关页——整篇保存还在做 Word 写回检查（约 1 s）时，关页的块级保存就到了。
    块级保存若立刻按旧修订合并，会再做一遍同样的检查（与前者抢 GIL、两者都变慢），提交时发现修订已变再重试，
    重开的读请求要等两次检查。先等整篇保存落库，再按新修订合并：内容相同时直接返回，不再检查。"""
    key = str(document_id)
    with _cond:
        return _cond.wait_for(lambda: key not in _content, timeout)


def wait_idle(document_id, timeout: float = WAIT_S) -> bool:
    """同一文档没有进行中的块级保存时立即返回 True；否则最多等 timeout 秒。"""
    key = str(document_id)
    with _cond:
        return _cond.wait_for(lambda: key not in _active, timeout)
