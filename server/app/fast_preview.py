"""快速预览（43 号接口说明）：不走 Word 往返，按对象直接给出可挂载的 HTML 片段与 Word 标题标记。

- 结构预览：每个存活对象的 Markdown 渲染成 HTML 片段，带 objectId/revision；按 (文档, 修订) 缓存。
- Word 正文样式标题：只解析一次原稿（按文件存储键缓存），用与整篇预览 data-word-heading 相同的
  word_heading_like 判断；正文段落的纯文本与原稿中“像标题”的段落一致时标出级别。
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path

_lock = threading.Lock()
_headings: OrderedDict[str, dict[str, int]] = OrderedDict()
_blocks: OrderedDict[tuple, list[dict]] = OrderedDict()
_warming: dict[tuple, threading.Event] = {}
_parsing: dict[str, threading.Event] = {}
MAX_FILES, MAX_DOCS = 64, 32
WARM_WAIT_S = 3.0
SHORT = 200  # 标题样式段落都很短；更长的正文不比较，省时间
_MARKUP = re.compile(r"\\(.)|[*_`]|<[^>]+>")


def bare(text: str) -> str:
    return "".join(_MARKUP.sub(lambda m: m.group(1) or "", text).split())


def _remember(cache: OrderedDict, key, value, limit: int):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)
    return value


def word_heading_texts(storage_key: str, path: Path) -> dict[str, int]:
    """原稿里 Word 认作标题的段落：{去空白纯文本: 级别}。按存储键缓存（原稿文件不可变）。"""
    with _lock:
        if storage_key in _headings:
            _headings.move_to_end(storage_key)
            return _headings[storage_key]
        pending = _parsing.get(storage_key)
        if pending is None:
            pending = _parsing[storage_key] = threading.Event()
            owner = True
        else:
            owner = False
    if not owner:
        # N1：打开时 /objects 与 /preview/blocks 同时到达，同一原稿只解析一次（另一个等它）
        pending.wait(30)
        with _lock:
            if storage_key in _headings:
                return _headings[storage_key]
    try:
        # N1：python-docx 解析原稿约 0.25 s 纯 CPU（慢机上更久），放进快速进程池，不在请求线程里抢 GIL
        from app import preview_cache
        return _remember(_headings, storage_key, preview_cache.fast_call(parse_headings, str(path)), MAX_FILES)
    finally:
        if owner:
            with _lock:
                _parsing.pop(storage_key, None)
            pending.set()


def parse_headings(path: str) -> dict[str, int]:
    """纯函数（快速进程池里执行）：原稿里 Word 认作标题的段落 {去空白纯文本: 级别}。"""
    from docx import Document
    from docx.text.paragraph import Paragraph
    from app.docx_preview import memoize_styles, word_heading_like
    result: dict[str, int] = {}
    try:
        doc = Document(path)
        memoize_styles(doc)
        for element in doc.element.body.iterchildren():
            if element.tag.endswith("}p"):
                paragraph = Paragraph(element, doc)
                text = bare(paragraph.text)
                if text and len(text) <= SHORT and (level := word_heading_like(paragraph)) is not None:
                    result.setdefault(text, level)
    except Exception:  # noqa: BLE001 - 原稿读不了时只是没有标记，不影响正文
        result = {}
    return result


def word_heading_levels(connection, document_id, rows) -> dict[str, int]:
    """{objectId: 级别}：正文段落对象中对应原稿 Word 标题样式的那些。"""
    from app.config import get_settings
    from app.files import resolve_object
    file = connection.execute(
        "SELECT f.storage_key FROM documents d JOIN files f ON f.id = d.original_docx_file_id "
        "WHERE d.id=%s AND f.status='stored'", (str(document_id),)).fetchone()
    if file is None:
        return {}
    texts = word_heading_texts(str(file[0]), resolve_object(get_settings().file_data_dir, file[0]))
    if not texts:
        return {}
    result = {}
    for row in rows:
        if row["kind"] == "paragraph" and len(row["body"]) <= SHORT * 2:
            level = texts.get(bare(row["body"]))
            if level is not None:
                result[str(row["id"])] = level
    return result


def _render(body: str) -> str:
    from app.document_selection import _render as render_markdown
    return render_markdown(body)


def blocks(connection, document_id, revision: int, rows) -> list[dict]:
    from app.timing_log import span
    key = (str(document_id), revision)
    with span("fast_blocks", doc=key[0][:8], rev=revision) as info:
        with _lock:
            if key in _blocks:
                _blocks.move_to_end(key)
                info["cache"] = "hit"
                return _blocks[key]
            pending = _warming.get(key)
            info["warming"] = sorted(rev for did, rev in _warming if did == key[0])
            info["cached"] = sorted(rev for did, rev in _blocks if did == key[0])
        if pending is not None and pending.wait(WARM_WAIT_S):
            # 保存后的后台预热正在算同一修订：等它算完直接用，不重复渲染
            with _lock:
                if key in _blocks:
                    _blocks.move_to_end(key)
                    info["cache"] = "warm_wait"
                    return _blocks[key]
        info["cache"] = "miss"
        return _compute(connection, key, document_id, rows)


def _compute(connection, key, document_id, rows) -> list[dict]:
    levels = word_heading_levels(connection, document_id, rows)
    result = []
    for row in rows:
        item = {"objectId": str(row["id"]), "revision": int(row["revision"]), "kind": row["kind"],
                "html": _render(row["body"])}
        if row["heading_level"] is not None:
            item["headingLevel"] = int(row["heading_level"])
        if (level := levels.get(str(row["id"]))) is not None:
            item["wordHeadingLevel"] = level
        result.append(item)
    return _remember(_blocks, key, result, MAX_DOCS)


def warm_after_commit(document_id, revision: int) -> threading.Thread | None:
    """块级保存提交后在后台线程（独立连接）预热快速预览：关页保存后马上重开，/preview/blocks 直接命中。

    只预热提交得到的那一修订；期间又有新提交时按读到的修订算（仍是有效缓存）。失败只是没预热。"""
    key = (str(document_id), int(revision))
    with _lock:
        if key in _blocks or key in _warming:
            return None
        done = _warming[key] = threading.Event()

    def run():
        try:
            from app.db import connect
            from app.document_objects import live_objects
            with connect() as connection:
                connection.execute("BEGIN")  # 修订号与对象行取自同一读快照
                current = connection.execute("SELECT revision FROM documents WHERE id=%s", (str(document_id),)).fetchone()
                if current is not None:
                    from app.timing_log import span
                    with span("fast_warm", doc=key[0][:8], rev=int(current["revision"]), asked=key[1]):
                        rows = live_objects(connection, document_id)
                        _compute(connection, (str(document_id), int(current["revision"])), document_id, rows)
                connection.rollback()
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响保存；真正请求时再算
            logging.getLogger(__name__).info("fast preview warm failed: %s", exc)
        finally:
            with _lock:
                _warming.pop(key, None)
            done.set()

    thread = threading.Thread(target=run, name=f"fast-preview-warm-{key[0][:8]}", daemon=True)
    thread.start()
    return thread


def clear() -> None:
    with _lock:
        _headings.clear()
        _blocks.clear()
