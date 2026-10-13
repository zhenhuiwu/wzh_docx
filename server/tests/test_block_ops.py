"""Spec 30 §4: block-ID operations for AI in-place editing."""

import json

import pytest

from app.block_ops import BlockOperationFormatError, apply_operations, parse_operations, scope_blocks
from app.errors import AppError

HEADING = {"id": "h", "body": "# 甲"}
BLOCKS = [{"id": "p1", "body": "一。"}, {"id": "p2", "body": "二。"}]


def ops(*items):
    return json.dumps({"operations": list(items)}, ensure_ascii=False)


def test_operations_compose_in_place_under_locked_heading():
    result = parse_operations(ops({"op": "insertAfter", "blockId": "h", "content": "零。"},
                                  {"op": "replace", "blockId": "p2", "content": "贰。"},
                                  {"op": "delete", "blockId": "p1"}), HEADING, BLOCKS)
    assert apply_operations(HEADING, BLOCKS, result) == "零。\n\n贰。"


@pytest.mark.parametrize("item", [{"op": "replace", "blockId": "h", "content": "新标题"},
                                  {"op": "delete", "blockId": "h"},
                                  {"op": "replace", "blockId": "other", "content": "x"}])
def test_locked_heading_and_out_of_scope_reject_everything(item):
    with pytest.raises(AppError) as error:
        parse_operations(ops(item), HEADING, BLOCKS)
    assert error.value.status_code == 409 and error.value.code == "AI_SCOPE_VIOLATION"


@pytest.mark.parametrize("text", ["不是 JSON", ops({"op": "replace", "blockId": "p1", "content": "## 新标题\n\n正文"}),
                                  ops(*[{"op": "insertAfter", "blockId": "p1", "content": "段。"}] * 21),
                                  ops({"op": "replace", "blockId": "p1", "content": "字" * 6001})])
def test_protocol_heading_and_limits_are_repairable(text):
    with pytest.raises(BlockOperationFormatError):
        parse_operations(text, HEADING, BLOCKS)


def test_scope_is_section_body_narrowed_to_writable_blocks():
    objects = [{"id": "h0", "kind": "heading", "headingIndex": 0, "body": "# 甲"},
               {"id": "a", "kind": "paragraph", "headingIndex": 0, "body": "一"},
               {"id": "b", "kind": "paragraph", "headingIndex": 0, "body": "二"},
               {"id": "h1", "kind": "heading", "headingIndex": 1, "body": "# 乙"},
               {"id": "c", "kind": "paragraph", "headingIndex": 1, "body": "三"}]
    heading, blocks = scope_blocks(objects, 0, writable={"b"})
    assert heading["id"] == "h0" and [item["id"] for item in blocks] == ["b"]
