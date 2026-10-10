"""位置与计数口径（01-阶段2设计 §4.9）。

location = {"headingPath": [...], "ordinal": n | None, "kind": "...", "label": str | None}
- headingPath：该块之前每个级别最近的标题原文，按级别由高到低。
- ordinal：最近一个标题之后、下一个标题之前的直属块里同 kind 从 1 计数；标题本身为 None。
- label：table/image 前后紧邻段落匹配“表 N/图 N”时取该段全文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LABEL = re.compile(r"^(表|图)\s*[0-9A-Za-z.\-－–]+")
_KIND_WORD = {"paragraph": "段", "list_item": "项", "table": "个表格", "image": "张图", "code": "个代码块", "other": "块"}


@dataclass(frozen=True)
class LocItem:
    kind: str                  # heading/paragraph/list_item/table/image/code/other
    text: str                  # 纯文字（标题文本、段落文本），用于 headingPath 与 label
    heading_level: int | None = None


def compute_locations(items: list[LocItem]) -> list[dict[str, object]]:
    path: list[tuple[int, str]] = []
    counts: dict[str, int] = {}
    result: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if item.kind == "heading" and item.heading_level:
            path = [entry for entry in path if entry[0] < item.heading_level]
            result.append({"headingPath": [text for _, text in path], "ordinal": None, "kind": "heading", "label": None})
            path.append((item.heading_level, item.text.strip()))
            counts = {}
            continue
        counts[item.kind] = counts.get(item.kind, 0) + 1
        label = None
        if item.kind in {"table", "image"}:
            for neighbour in (index - 1, index + 1):
                if 0 <= neighbour < len(items) and items[neighbour].kind == "paragraph" \
                        and _LABEL.match(items[neighbour].text.strip()):
                    label = items[neighbour].text.strip()
                    break
        result.append({"headingPath": [text for _, text in path], "ordinal": counts[item.kind],
                       "kind": item.kind, "label": label})
    return result


def location_text(location: dict[str, object]) -> str:
    """面向用户的位置文字（不含任何内部标识）。"""
    path = " › ".join(location.get("headingPath") or []) or "文档开头（第一个标题之前）"
    if location.get("kind") == "heading":
        return f"{path} · 标题本身"
    if location.get("label"):
        return f"{path} · {location['label']}"
    word = _KIND_WORD.get(str(location.get("kind")), "块")
    return f"{path} · 第 {location.get('ordinal')} {word}"


def html_block_items(blocks: list) -> list[LocItem]:
    """Python-Markdown 顶层 HTML 块 → LocItem（导出与 Word 预检使用的同一切分）。"""
    items: list[LocItem] = []
    for element in blocks:
        tag = element.tag
        text = " ".join("".join(element.itertext()).split())
        if re.fullmatch(r"h[1-6]", tag):
            items.append(LocItem("heading", text, int(tag[1])))
        elif tag == "table":
            items.append(LocItem("table", text))
        elif tag in {"ul", "ol"}:
            items.append(LocItem("list_item", text))
        elif tag == "pre":
            items.append(LocItem("code", text))
        elif tag == "p" and element.xpath("./img") and not text:
            items.append(LocItem("image", text))
        elif tag == "p":
            items.append(LocItem("paragraph", text))
        else:
            items.append(LocItem("other", text))
    return items
