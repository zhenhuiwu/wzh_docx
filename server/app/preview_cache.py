"""整篇版式预览（Word 原稿 + 当前正文）的进程内缓存与后台预热（P0-2）。

- 渲染是正文、原稿文件、图片、标题识别方式的纯函数：按这些内容的摘要做键，命中直接返回。
- 同一键并发请求共用一次渲染（in-flight future），不会重复做 10 秒级的工作。
- 渲染在独立进程池执行（默认 4 进程，PREVIEW_WORKERS 可调），不占服务进程的 GIL，其他接口不受影响，不同文档并行。
- 打开文档（GET /documents/{id}）时提交后台预热，前端随后请求 /preview 多半已命中或在等同一次渲染。
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import threading
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ENTRIES = 16
_lock = threading.Lock()
_done: OrderedDict[str, str] = OrderedDict()
_running: dict[str, Future] = {}
_pool: ProcessPoolExecutor | None = None
_fast: ProcessPoolExecutor | None = None


def workers() -> int:
    """渲染是纯 CPU（python-docx/lxml），线程受 GIL 限制只能串行；用独立进程并行，默认 4 个。"""
    return max(1, int(os.environ.get("PREVIEW_WORKERS", "4")))


def _executor() -> ProcessPoolExecutor:
    """调用方持锁。spawn：不继承服务进程的线程与数据库连接。"""
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=workers(), mp_context=multiprocessing.get_context("spawn"))
    return _pool


def fast_workers() -> int:
    return max(1, int(os.environ.get("FAST_PREVIEW_WORKERS", "2")))


def _fast_executor() -> ProcessPoolExecutor:
    """N1：快速路径（编辑区可编辑前要用的原稿标题表等）专用的小进程池，与整篇渲染池分开：
    整篇渲染每篇 5–10 s，4 篇同时打开时会占满渲染池，快速路径排在它后面就等于被饿死。"""
    global _fast
    with _lock:
        if _fast is None:
            _fast = ProcessPoolExecutor(max_workers=fast_workers(), mp_context=multiprocessing.get_context("spawn"))
        return _fast


def _noop() -> None:
    return None


def prestart_fast() -> None:
    """服务启动时预先拉起快速池的子进程（spawn 要导入 python-docx，约 1 s），首个打开文档的人不用等。"""
    try:
        pool = _fast_executor()
        for future in [pool.submit(_noop) for _ in range(fast_workers())]:
            future.result(timeout=60)
    except Exception as exc:  # noqa: BLE001 - 预启动失败时首次使用再拉起
        logger.info("fast pool prestart failed: %s", exc)


def fast_call(fn, *args):
    """在快速池执行 fn(*args)（纯 CPU、可序列化）；池损坏时退回本进程执行，结果相同。"""
    global _fast
    try:
        return _fast_executor().submit(fn, *args).result()
    except RuntimeError:  # BrokenProcessPool 也是 RuntimeError 子类
        with _lock:
            _fast = None
        return fn(*args)


def render(path: str, markdown: str, images: list | None, outline_headings: bool, selection: bool) -> str:
    """在子进程中执行：Word 原稿 + 正文 → 预览 HTML（selection 时再加选区标注）。"""
    from app.docx_preview import render_document_html
    html = render_document_html(Path(path), markdown, images, outline_headings=outline_headings)
    if selection:
        from app.document_selection import decorate_selection_html
        html = decorate_selection_html(markdown, html)
    return html


Job = tuple  # (path, markdown, images, outline_headings, selection)


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


def _submit(key: str, job: Job) -> Future:
    """调用方持锁。"""
    try:
        future = _executor().submit(render, *job)
    except RuntimeError:  # 进程池损坏（子进程被杀等）：重建一次
        global _pool
        _pool = None
        future = _executor().submit(render, *job)
    _running[key] = future
    future.add_done_callback(lambda done: _finish(key, done))
    return future


def get(key: str, job: Job) -> str:
    """命中直接返回；否则等待（或发起）同一键的那一次渲染。渲染异常原样抛出。"""
    with _lock:
        if key in _done:
            _done.move_to_end(key)
            return _done[key]
        future = _running.get(key) or _submit(key, job)
    return future.result()


def warm(key: str, job: Job) -> None:
    """后台预热；已缓存或正在渲染时不重复提交。异常只记日志（真正请求时会再报给用户）。"""
    with _lock:
        if key in _done or key in _running:
            return
        future = _submit(key, job)
    future.add_done_callback(lambda done: done.exception() and logger.info("preview warm failed: %s", done.exception()))


_decorated: OrderedDict[tuple, tuple] = OrderedDict()


def decorated(key: str, html: str, rows: list) -> tuple[str, bool]:
    """N1：整篇预览加对象 ID（lxml 解析整篇 + 逐对象取纯文本，大文档约 1 s 纯 CPU）放进进程池执行，
    不在请求线程里抢 GIL（同时打开的其他文档的快速预览、/objects 因此不被拖慢）。按 (预览键, 对象 ID+修订) 缓存。"""
    from app.document_objects import decorate_html
    signature = hashlib.sha256("\0".join(f"{object_id}:{revision}" for object_id, revision, _ in rows).encode()).hexdigest()
    cache_key = (key, signature)
    with _lock:
        if cache_key in _decorated:
            _decorated.move_to_end(cache_key)
            return _decorated[cache_key]
    try:
        with _lock:
            future = _executor().submit(decorate_html, html, rows)
        result = future.result()
    except RuntimeError:  # 进程池损坏：退回本进程执行
        result = decorate_html(html, rows)
    with _lock:
        _decorated[cache_key] = result
        while len(_decorated) > MAX_ENTRIES:
            _decorated.popitem(last=False)
    return result


def clear() -> None:
    with _lock:
        _done.clear()
        _decorated.clear()


def shutdown() -> None:
    global _pool, _fast
    with _lock:
        pools, _pool, _fast = (_pool, _fast), None, None
    for pool in pools:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
