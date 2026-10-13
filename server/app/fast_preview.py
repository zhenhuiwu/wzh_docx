"""快速预览（43 号接口说明）：不走 Word 往返，按对象直接给出可挂载的 HTML 片段与 Word 标题标记。

- 结构预览：每个存活对象的 Markdown 渲染成 HTML 片段，带 objectId/revision；按 (文档, 修订) 缓存。
- Word 正文样式标题：只解析一次原稿（按文件存储键缓存），用与整篇预览 data-word-heading 相同的
  word_heading_like 判断；正文段落的纯文本与原稿中“像标题”的段落一致时标出级别。
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path

_lock = threading.Lock()
_headings: OrderedDict[str, dict[str, int]] = OrderedDict()
_blocks: OrderedDict[tuple, list[dict]] = OrderedDict()
MAX_FILES, MAX_DOCS = 64, 32
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
    from docx import Document
    from docx.text.paragraph import Paragraph
    from app.docx_preview import memoize_styles, word_heading_like
    result: dict[str, int] = {}
    try:
        doc = Document(str(path))
        memoize_styles(doc)
        for element in doc.element.body.iterchildren():
            if element.tag.endswith("}p"):
                paragraph = Paragraph(element, doc)
                text = bare(paragraph.text)
                if text and len(text) <= SHORT and (level := word_heading_like(paragraph)) is not None:
                    result.setdefault(text, level)
    except Exception:  # noqa: BLE001 - 原稿读不了时只是没有标记，不影响正文
        result = {}
    return _remember(_headings, storage_key, result, MAX_FILES)


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
    key = (str(document_id), revision)
    with _lock:
        if key in _blocks:
            _blocks.move_to_end(key)
            return _blocks[key]
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


def clear() -> None:
    with _lock:
        _headings.clear()
        _blocks.clear()
