"""AI in-place editing keyed by document block (object) IDs (spec 30 §4).

The model sees the blocks of one section and returns operations
``replace(blockId)``, ``insertAfter(blockId)`` and ``delete(blockId)``.
Headings are locked: they may only serve as an ``insertAfter`` anchor.
Any operation outside the section whitelist rejects the whole result.
"""

from __future__ import annotations

import json
import re

from app.document_rules import DraftFormatError
from app.errors import AppError

MAX_OPERATIONS = 20  # spec 30 Q-8: one section, at most 20 paragraphs
MAX_CHARS = 6000     # spec 30 Q-8: at most 6000 characters per request
OPS = {"replace", "insertAfter", "delete"}
_HEADING = re.compile(r"^\s{0,3}#{1,6}[ \t]", re.MULTILINE)


class BlockOperationFormatError(DraftFormatError):
    """The response is not a well-formed operation list; one model correction is allowed."""


def scope_blocks(objects: list[dict], heading_index: int, writable: set[str] | None = None,
                 locked: set[str] | None = None) -> tuple[dict | None, list[dict]]:
    """Return the locked heading and the editable body blocks of one section.

    The section is the heading's own body up to the next heading, matching
    the server's headingIndex semantics. ``writable`` (A07 assignments)
    narrows the whitelist to blocks the user may change. ``locked`` are
    Word heading paragraphs kept as body (e.g. "1.1 标识"): they stay in the
    section for composition but are marked ``locked`` and are only anchors.
    """
    heading = None
    blocks = []
    count = -1
    for item in objects:
        if item["kind"] == "heading":
            count += 1
            if count == heading_index:
                heading = item
            continue
        if item["headingIndex"] == heading_index and (writable is None or item["id"] in writable or item["id"] in (locked or set())):
            blocks.append({**item, "locked": True} if item["id"] in (locked or set()) else item)
    return heading, blocks


def prompt_blocks(heading: dict | None, blocks: list[dict]) -> list[dict]:
    result = []
    if heading is not None:
        result.append({"blockId": heading["id"], "kind": "heading", "locked": True, "text": heading["body"]})
    result += [{"blockId": item["id"], "kind": "heading" if item.get("locked") else item["kind"], "text": item["body"],
                **({"locked": True} if item.get("locked") else {})} for item in blocks]
    return result


def parse_operations(text: str, heading: dict | None, blocks: list[dict]) -> list[dict]:
    """Validate a model response. Scope violations raise AppError 409 (no repair)."""
    try:
        value = json.loads(text)
    except ValueError:
        raise BlockOperationFormatError("修改结果未按块操作协议返回 JSON。") from None
    if not isinstance(value, dict) or set(value) != {"operations"} or not isinstance(value["operations"], list):
        raise BlockOperationFormatError("修改结果需要 operations 数组。")
    allowed = {item["id"] for item in blocks if not item.get("locked")}
    locked = ({heading["id"]} if heading is not None else set()) | {item["id"] for item in blocks if item.get("locked")}
    operations, touched, chars = [], set(), 0
    for item in value["operations"]:
        if not isinstance(item, dict) or item.get("op") not in OPS or not isinstance(item.get("blockId"), str):
            raise BlockOperationFormatError("每个操作需要 op（replace/insertAfter/delete）与 blockId。")
        op, block_id = item["op"], item["blockId"]
        content = item.get("content", "")
        if set(item) - {"op", "blockId", "content"} or not isinstance(content, str):
            raise BlockOperationFormatError("操作只能包含 op、blockId、content。")
        if block_id in locked and op != "insertAfter" or block_id not in allowed | locked:
            raise AppError(409, "AI_SCOPE_VIOLATION", "AI 返回的位置不对，已全部放弃，请重试。")
        if op == "delete":
            content = ""
        elif not content.strip():
            raise BlockOperationFormatError("replace 与 insertAfter 需要非空 content。")
        elif _HEADING.search(content):
            raise BlockOperationFormatError("标题已锁定，content 不能包含标题。")
        if op != "insertAfter":
            if block_id in touched:
                raise BlockOperationFormatError("同一块只能 replace 或 delete 一次。")
            touched.add(block_id)
        chars += len(content)
        operations.append({"op": op, "blockId": block_id, "content": content.strip()})
    if len(operations) > MAX_OPERATIONS or chars > MAX_CHARS:
        raise BlockOperationFormatError(f"一次最多修改 {MAX_OPERATIONS} 段、{MAX_CHARS} 字，请只保留最必要的修改。")
    return operations


def apply_operations(heading: dict | None, blocks: list[dict], operations: list[dict]) -> str:
    """Compose the section body after all operations (for validation and Word checks)."""
    after: dict[str, list[str]] = {}
    changed: dict[str, str | None] = {}
    for item in operations:
        if item["op"] == "insertAfter":
            after.setdefault(item["blockId"], []).append(item["content"])
        else:
            changed[item["blockId"]] = item["content"] or None
    parts = list(after.get(heading["id"], [])) if heading is not None else []
    for block in blocks:
        value = changed.get(block["id"], block["body"])
        if value is not None:
            parts.append(value)
        parts += after.get(block["id"], [])
    return "\n\n".join(parts)
