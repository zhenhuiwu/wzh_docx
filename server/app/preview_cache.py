"""整篇版式预览（Word 原稿 + 当前正文）的进程内缓存与后台预热（P0-2）。

- 渲染是正文、原稿文件、图片、标题识别方式的纯函数：按这些内容的摘要做键，命中直接返回。
- 同一键并发请求共用一次渲染（in-flight future），不会重复做 10 秒级的工作。
- 打开文档（GET /documents/{id}）时提交后台预热，前端随后请求 /preview 多半已命中或在等同一次渲染。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)

MAX_ENTRIES = 16
_lock = threading.Lock()
_done: OrderedDict[str, str] = OrderedDict()
_running: dict[str, Future] = {}
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview")


def key_of(storage_key: str, markdown: str, images: list | None, outline_headings: bool) -> str:
    digest = hashlib.sha256()
    for part in (storage_key, "1" if outline_headings else "0", markdown or "",
                 json.dumps(images or [], sort_keys=True, ensure_ascii=False, default=str)):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _finish(key: str, future: Future) -> None:
    with _lock:
        _running.pop(key, None)
        if future.exception() is None:
            _done[key] = future.result()
            _done.move_to_end(key)
            while len(_done) > MAX_ENTRIES:
                _done.popitem(last=False)


def _submit(key: str, render: Callable[[], str]) -> Future:
    """调用方持锁。"""
    future = _pool.submit(render)
    _running[key] = future
    future.add_done_callback(lambda done: _finish(key, done))
    return future


def get(key: str, render: Callable[[], str]) -> str:
    """命中直接返回；否则等待（或发起）同一键的那一次渲染。渲染异常原样抛出。"""
    with _lock:
        if key in _done:
            _done.move_to_end(key)
            return _done[key]
        future = _running.get(key) or _submit(key, render)
    return future.result()


def warm(key: str, render: Callable[[], str]) -> None:
    """后台预热；已缓存或正在渲染时不重复提交。异常只记日志（真正请求时会再报给用户）。"""
    with _lock:
        if key in _done or key in _running:
            return
        future = _submit(key, render)
    future.add_done_callback(lambda done: done.exception() and logger.info("preview warm failed: %s", done.exception()))


def clear() -> None:
    with _lock:
        _done.clear()
