"""Test helper: answer like the model for a section that already has body (spec 30 §4)."""

import json


def section_answer(kwargs: dict, text: str) -> str:
    """Prose for an empty section; for a section with body, block operations that
    replace the first block with ``text`` and delete the rest, so the composed body is ``text``."""
    scope = kwargs.get("block_scope")
    if scope is None:
        return text
    blocks = scope["blocks"]
    operations = [{"op": "replace", "blockId": blocks[0]["id"], "content": text}]
    operations += [{"op": "delete", "blockId": item["id"]} for item in blocks[1:]]
    return json.dumps({"operations": operations}, ensure_ascii=False)
