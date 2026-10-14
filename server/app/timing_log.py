"""QA 复验 14 P2：按环境变量开启的后端计时日志（JSON Lines）。DOCWB_TIMING_LOG=<文件路径> 时记录，否则全部为空操作。

记录关页保存各阶段、patch_inflight 等待、/preview/blocks（等待、缓存命中/预热等待/未命中、计算），用于在 QA 环境里
分清重开慢是在等保存、缓存未命中还是没预热。"""
import json
import os
import threading
import time
from contextlib import contextmanager

_PATH = os.environ.get("DOCWB_TIMING_LOG") or None
_lock = threading.Lock()


def enabled() -> bool:
    return _PATH is not None


def event(ev: str, **fields) -> None:
    if _PATH is None:
        return
    fields = {"ev": ev, "t": round(time.time(), 3), "thr": threading.current_thread().name[:24], **fields}
    line = json.dumps(fields, ensure_ascii=False, default=str) + "\n"
    with _lock, open(_PATH, "a", encoding="utf-8") as handle:
        handle.write(line)


@contextmanager
def span(ev: str, **fields):
    """记一段耗时；块内可往 yield 出的 dict 里补字段（如 outcome）。"""
    if _PATH is None:
        yield {}
        return
    extra: dict = {}
    start = time.perf_counter()
    try:
        yield extra
    finally:
        event(ev, ms=round((time.perf_counter() - start) * 1000, 1), **fields, **extra)
