"""A07 修复第 4 轮（27 复验：变异 S3/F4，M-3 残留 / 28 M-1''）。"""

from __future__ import annotations

import dataclasses

import pytest

from app import document_assignments as da
from app.db import connect
from app.errors import AppError
from tests.test_assignments_a07 import ctx  # noqa: F401

pytestmark = pytest.mark.enforced_new_documents


def _body_after(pairs, title):
    for index, (_i, block) in enumerate(pairs):
        if block.kind == "heading" and title in block.body:
            return next((object_id, b) for object_id, b in pairs[index + 1:] if b.kind != "heading")
    raise AssertionError(title)


# ---------------------------------------------------------------- 变异 S3：标题文字对不上不能放行

def test_s3_heading_text_mismatch_is_refused_before_model(ctx):
    did = ctx.as_("owner").create()["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    pairs = ctx.state(did)[2]
    headings = [block for _i, block in pairs if block.kind == "heading"]
    mine = next(i for i, block in enumerate(headings) if "功能需求" in block.body)
    other = next(i for i, block in enumerate(headings) if "性能需求" in block.body)
    with connect() as connection:
        actor = ctx.users["wang"]
        da.ai_precheck(connection, did, actor, mine, False, heading_text=da._plain_heading(headings[mine].body))   # 对得上：放行
        with pytest.raises(AppError) as caught:   # 序号指向自己的章，文字却是不存在的标题 → 不放行
            da.ai_precheck(connection, did, actor, mine, False, heading_text="并不存在的标题")
        assert caught.value.status_code == 409 and caught.value.details["reason"] == "ai_scope"
        with pytest.raises(AppError) as caught:   # 序号指向自己的章，文字是别人的章 → 按文字找到别人的章，拒绝
            da.ai_precheck(connection, did, actor, mine, False, heading_text=da._plain_heading(headings[other].body))
        assert caught.value.status_code == 409 and caught.value.details["violations"]


# ---------------------------------------------------------------- 变异 F4：我删了、别人同时改了 → 不能静默丢掉别人的修改

def test_f4_i_deleted_other_edited_is_not_silently_lost(ctx):
    did = ctx.as_("owner").create()["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.as_("wang")
    _prefix, base_rev, pairs, _ = ctx.state(did)
    target, block = _body_after(pairs, "功能需求")
    ctx.as_("owner")
    response = ctx.save(did, lambda ps: [(i, dataclasses.replace(b, body=b.body + "（负责人改）") if i == target else b) for i, b in ps])
    assert response.status_code == 200, response.text
    ctx.as_("wang")   # 按开始编辑时的修订提交“删掉这段”
    response = ctx.save(did, lambda _ps: [(i, b) for i, b in pairs if i != target], expected=base_rev)
    markdown = ctx.ok("GET", f"/documents/{did}")["markdownContent"]
    assert response.status_code == 409 or (block.body + "（负责人改）") in markdown, response.text
    if response.status_code == 409:
        assert response.json()["error"]["code"] in {"OBJECT_CONFLICT", "REVISION_CONFLICT"}
        assert (block.body + "（负责人改）") in markdown
