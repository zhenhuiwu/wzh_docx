"""A07 修复第 1 轮（27 审查 M-1/M-3 与变异缺口 M5–M9，28 L-1）。"""

from __future__ import annotations

import pytest

from app import document_assignments as da
from tests.test_assignments_a07 import BASE, EXPLICIT, H, N, _counts, ctx, kinds, replace_text  # noqa: F401

pytestmark = pytest.mark.enforced_new_documents


# ---------------------------------------------------------------- 变异缺口（27 §7）

def test_m5_structure_change_of_others_block_is_modify():
    changed = [n if n.id != "p31" else da.Node("p31", "list_item", None, n.body) for n in BASE]   # 文字不变，改成列表项
    assert kinds(da.check(BASE, changed, EXPLICIT, "A")) == [("modify", "p31")]
    own = [n if n.id != "p32" else da.Node("p32", "list_item", None, n.body) for n in BASE]
    assert da.check(BASE, own, EXPLICIT, "A") == []


def test_m6_moving_explicit_heading_is_owner_only():
    order = [BASE[0], BASE[3], BASE[4], BASE[5], BASE[1], BASE[2], BASE[6], BASE[7]]            # 3.2 整章挪到 3.1 之前
    found = kinds(da.check(BASE, order, EXPLICIT, "A"))
    assert {("heading_structure", "h31"), ("heading_structure", "h32")} & set(found)   # 有显式分配的标题换位只有负责人能做
    assert any(kind == "heading_structure" for kind, _id in kinds(da.check(BASE, order, EXPLICIT, "Z")))


def test_m7_m8_sanitize_dedupes_and_checks_kind():
    live = {"a": "paragraph", "h": "heading"}
    assert da.sanitize_identities(live, ["paragraph", "paragraph"], ["a", "a"]) == ["a", None]       # 重复只认第一个
    assert da.sanitize_identities(live, ["paragraph", "heading"], ["h", "a"]) == [None, None]      # 类别不符
    assert da.sanitize_identities(live, ["paragraph"], ["zzz"]) == [None]                          # 外来 id


def test_m9_inheritance_from_following_block_requires_actor():
    explicit = {"h32": frozenset({"A"}), "p32": frozenset({"B"})}
    start = BASE[:4] + [N("new")] + BASE[4:]                     # 章节开头新增：看后一块 p32（B 的例外）
    assert "new" in da.plan_inheritance({n.id for n in BASE}, start, explicit, "B")
    assert "new" not in da.plan_inheritance({n.id for n in BASE}, start, explicit, "A")
    assert kinds(da.check(BASE, start, explicit, "A")) == []     # A 在自己章开头插入：归章节 A，不继承 B


# ---------------------------------------------------------------- 27 M-1 服务端兜底

def test_owner_save_with_misaligned_ids_keeps_assignments(ctx):
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.assign(did, [ctx.heading(did, "性能需求")], ["sun"])
    before = len(ctx.ok("GET", f"/documents/{did}/assignments")["explicit"])
    # 客户端把所有 id 往后错一位（大文档配错的情形）：标题被换成别的 id
    def shifted(pairs):
        ids = [object_id for object_id, _b in pairs]
        ids = [None] + ids[:-1]
        return [(i, b) for i, (_old, b) in zip(ids, replace_text(pairs, "范围正文", "负责人改了"))]
    response = ctx.save(did, shifted)
    assert response.status_code == 200, response.text
    view = ctx.ok("GET", f"/documents/{did}/assignments")
    assert len(view["explicit"]) == before == 2


# ---------------------------------------------------------------- 27 M-3 / 28 L-1：AI 预检与留存

def test_ai_draft_precheck_does_not_call_model(ctx, monkeypatch):
    calls = []
    import app.documents as documents
    monkeypatch.setattr(documents, "require_ai", lambda *a, **k: calls.append("ai"))
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.as_("wang")
    revision = ctx.ok("GET", f"/documents/{did}")["revision"]
    headings = [b.body for _i, b in ctx.state(did)[2] if b.kind == "heading"]
    other = next(i for i, body in enumerate(headings) if "性能需求" in body)
    response = ctx.req("POST", f"/documents/{did}/sections/draft", {"expectedRevision": revision, "headingIndex": other})
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "ai_scope", response.text
    response = ctx.req("POST", f"/documents/{did}/sections/draft", {"expectedRevision": revision, "scope": "document"})
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "document_scope"
    assert calls == []                                            # 模型没有被调用
    mine = next(i for i, body in enumerate(headings) if "功能需求" in body)
    ctx.req("POST", f"/documents/{did}/sections/draft", {"expectedRevision": revision, "headingIndex": mine})
    assert calls == ["ai"]                                        # 自己的章节照常走到模型


def test_derived_write_paths_store_rejected(ctx):
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.as_("wang")
    headings = [b.body for _i, b in ctx.state(did)[2] if b.kind == "heading"]
    other = next(i for i, body in enumerate(headings) if "性能需求" in body)
    revision = ctx.ok("GET", f"/documents/{did}")["revision"]
    before = _counts(did)[4]
    response = ctx.req("POST", f"/documents/{did}/sections/insert",
                       {"expectedRevision": revision, "headingIndex": other, "content": "AI 写进别人章节的文字", "mode": "append"})
    assert response.status_code == 409, response.text
    rid = response.json()["error"]["details"]["rejectedSaveId"]
    assert "AI 写进别人章节的文字" in ctx.ok("GET", f"/documents/{did}/rejected-saves/{rid}")["markdown"]
    response = ctx.req("POST", f"/documents/{did}/headings",
                       {"expectedRevision": revision, "afterIndex": other, "placement": "child", "title": "越权子标题"})
    assert response.status_code == 409 and response.json()["error"]["details"].get("rejectedSaveId"), response.text
    assert _counts(did)[4] == before + 2


# ---------------------------------------------------------------- 27 复验 M-3 / N-3

def _section_body(ctx, did, heading):
    pairs = ctx.state(did)[2]
    for index, (_i, block) in enumerate(pairs):
        if block.kind == "heading" and heading in block.body:
            return next(b.body for _j, b in pairs[index + 1:] if b.kind != "heading")
    raise AssertionError(heading)


def test_selection_ai_precheck_by_selected_blocks(ctx, monkeypatch):
    calls = []
    import app.documents as documents
    from app.document_selection import selection_blocks
    monkeypatch.setattr(documents, "require_ai", lambda *a, **k: calls.append("ai"))
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.as_("wang")
    record = ctx.ok("GET", f"/documents/{did}")

    def body(text, extra=None):
        block = next(item for item in selection_blocks(record["markdownContent"]) if item["text"].strip() == text.strip())
        word = block["text"][:2]
        return {"expectedRevision": record["revision"], "scope": "selection", "selectedText": word,
                "selection": {"blockId": block["blockId"], "start": 0, "end": len(word), "text": word}, **(extra or {})}
    other = next(i for i, b in ctx.state(did)[2] if b.kind != "heading" and b.body == _section_body(ctx, did, "性能需求"))
    mine = next(i for i, b in ctx.state(did)[2] if b.kind != "heading" and b.body == _section_body(ctx, did, "功能需求"))
    response = ctx.req("POST", f"/documents/{did}/sections/draft", body(_section_body(ctx, did, "性能需求"), {"selectionBlockIds": [other]}))
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "ai_scope", response.text
    assert calls == []
    response = ctx.req("POST", f"/documents/{did}/sections/draft", body(_section_body(ctx, did, "功能需求"), {"selectionBlockIds": [mine, other]}))
    assert response.status_code == 409, response.text                        # 前端报的选区块里有别人的
    assert calls == []
    ctx.req("POST", f"/documents/{did}/sections/draft", body(_section_body(ctx, did, "功能需求"), {"selectionBlockIds": [mine]}))
    assert calls == ["ai"]


def test_document_instructions_owner_only_on_enforced(ctx):
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    ctx.as_("wang")
    revision = ctx.ok("GET", f"/documents/{did}")["revision"]
    response = ctx.req("PATCH", f"/documents/{did}/writing-skill", {"expectedRevision": revision, "mode": "auto", "documentInstructions": "全篇用第三人称"})
    assert response.status_code == 403 and response.json()["error"]["details"]["reason"] == "assignment", response.text
    ctx.as_("owner")
    response = ctx.req("PATCH", f"/documents/{did}/writing-skill", {"expectedRevision": revision, "mode": "auto", "documentInstructions": "全篇用第三人称"})
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------- 29 复验：停用 / 启用负责人触发重取

def test_disable_and_enable_bump_assignments_revision(ctx):
    doc = ctx.as_("owner").create()
    did = doc["id"]
    ctx.assign(did, [ctx.heading(did, "功能需求")], ["wang"])
    first = ctx.ok("GET", f"/documents/{did}/assignments")["assignmentsRevision"]
    ctx.as_("admin")
    wang = str(ctx.users["wang"])

    def revision_of():
        items = ctx.ok("GET", "/users?pageSize=50")["items"]
        return next(item["revision"] for item in items if item["id"] == wang)
    ctx.ok("POST", f"/users/{wang}/disable", {"reason": "测试", "expectedRevision": revision_of()})
    view = ctx.ok("GET", f"/documents/{did}/assignments")
    assert view["assignmentsRevision"] > first
    explicit = next(item for item in view["explicit"] if item["assignees"][0]["id"] == wang)
    assert explicit["assignees"][0]["userActive"] is False            # 面板据此显示“负责人已停用”
    disabled = view["assignmentsRevision"]
    ctx.ok("POST", f"/users/{wang}/enable", {"reason": "测试", "expectedRevision": revision_of()})
    assert ctx.ok("GET", f"/documents/{did}/assignments")["assignmentsRevision"] > disabled
