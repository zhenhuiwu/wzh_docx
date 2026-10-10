"""Markdown 顶层块切分、拼接与语义规范键（阶段2，01 §3.1）。

- split(md) 逐字节可逆：assemble(split(md)) == md。每块存 body（块本身，不含结尾换行）
  与 gap_after（其后的换行/空行原样），文档级 prefix 存首块前的空白。CRLF 原样保留。
- 块粒度：ATX 标题、段落、顶层列表条目（续行与嵌套归属父条目）、整张表格、围栏代码、
  独立成段的 docx-image 图片、其他（引用、HTML 块、Setext 标题、缩进代码）。
- 规范键 canonical_key：单独渲染该块后复用导出用的 docx_export._key，
  turndown 的转义/列表符差异不算修改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
_LIST = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_IMAGE = re.compile(r"^!\[[^\]\n]*\]\(docx-image:[^)\s]+\)$")
_HTML = re.compile(r"^ {0,3}<[A-Za-z!/?]")

KINDS = ("heading", "paragraph", "list_item", "table", "image", "code", "other")


@dataclass(frozen=True)
class Block:
    kind: str
    body: str
    gap_after: str
    heading_level: int | None = None


def _blank(line: str) -> bool:
    return not line.strip()


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def split(markdown: str) -> tuple[str, list[Block]]:
    lines = markdown.splitlines(keepends=True)
    spans: list[tuple[int, int, str, int | None]] = []   # [start_line, end_line) + kind + level
    index = 0
    while index < len(lines):
        line = _content(lines[index])
        if _blank(line):
            index += 1
            continue
        start = index
        level: int | None = None
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*$")
            index += 1
            while index < len(lines) and not closing.match(_content(lines[index])):
                index += 1
            index = min(index + 1, len(lines))
            kind = "code"
        elif heading := _ATX.match(line):
            index += 1
            kind, level = "heading", len(heading.group(1))
        elif "|" in line and index + 1 < len(lines) and _TABLE_RULE.match(_content(lines[index + 1])) \
                and "-" in lines[index + 1]:
            index += 2
            while index < len(lines) and not _blank(lines[index]):
                index += 1
            kind = "table"
        elif _LIST.match(line):
            index = _list_item_end(lines, index)
            kind = "list_item"
        elif line.lstrip().startswith(">") or _HTML.match(line) or line.startswith("    ") or line.startswith("\t"):
            while index < len(lines) and not _blank(lines[index]):
                index += 1
            kind = "other"
        else:
            index += 1
            kind = "paragraph"
            while index < len(lines) and not _blank(lines[index]):
                current = _content(lines[index])
                if _ATX.match(current) or _FENCE.match(current):
                    break
                if _SETEXT.match(current) and current.strip()[0] in "=-":
                    index += 1
                    kind = "other"
                    break
                index += 1
            text = "".join(lines[start:index]).strip()
            if kind == "paragraph" and _IMAGE.match(text):
                kind = "image"
        spans.append((start, index, kind, level))

    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    blocks: list[Block] = []
    prefix = markdown[: offsets[spans[0][0]]] if spans else markdown
    for position, (start, end, kind, level) in enumerate(spans):
        raw = markdown[offsets[start]: offsets[end]]
        body = raw.rstrip("\r\n")
        body_end = offsets[start] + len(body)
        gap_end = offsets[spans[position + 1][0]] if position + 1 < len(spans) else len(markdown)
        blocks.append(Block(kind, body, markdown[body_end:gap_end], level))
    return prefix, blocks


def _list_item_end(lines: list[str], index: int) -> int:
    """顶层列表条目：首行 + 续行/嵌套行；空行之后只有缩进行才继续属于本条目。"""
    indent = len(lines[index]) - len(lines[index].lstrip(" "))
    index += 1
    while index < len(lines):
        current = _content(lines[index])
        if _blank(current):
            ahead = index
            while ahead < len(lines) and _blank(lines[ahead]):
                ahead += 1
            if ahead < len(lines) and re.match(r"^( {2,}|\t)\S", _content(lines[ahead])):
                index = ahead
                continue
            break
        deeper = len(current) - len(current.lstrip(" ")) > indent
        if (_LIST.match(current) and not deeper) or _ATX.match(current) or (_FENCE.match(current) and not deeper):
            break
        index += 1
    return index


def assemble(prefix: str, blocks: list[Block] | list) -> str:
    return prefix + "".join(block.body + block.gap_after for block in blocks)


def heading_text(body: str) -> str:
    text = re.sub(r"^ {0,3}#{1,6}[ \t]*", "", body.strip())
    text = re.sub(r"[ \t]+#+[ \t]*$", "", text)
    return re.sub(r"\\([\\`*_{}\[\]<>|#()+\-.!])", r"\1", text).strip()


@lru_cache(maxsize=4096)
def canonical_key(body: str) -> tuple:
    """语义规范键：渲染后的标签、文字、图片引用与行内格式。"""
    from app.docx_export import _html_blocks, _key

    elements = _html_blocks(body)
    return tuple(_key(element) for element in elements) or (("empty",),)


def plain_text(body: str) -> str:
    from app.docx_export import _html_blocks

    return " ".join(" ".join("".join(element.itertext()) for element in _html_blocks(body)).split())
