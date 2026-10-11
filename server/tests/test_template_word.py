"""A06 模板 Word 引擎：20 份真实 438C 模板逐份“改字/改级别/新增/删除/单元格/移章/改样式”→ 生成 Word → 往返核对。"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app import template_word as tw
from app.template_import import parse_template

TEMPLATES = sorted((Path(__file__).resolve().parents[2] / "438C-2021全套模板").glob("*.docx"))


def _edit(content: bytes) -> tuple[dict, dict, list[str], dict]:
    structure = parse_template(content)
    structure["reviewed"] = True
    analysis = tw._analysis(content)
    draft = copy.deepcopy(structure)
    blocks = draft["blocks"]
    top = [block for block in blocks if not block["tableId"]]
    heads = [block for block in top if block["kind"] == "heading"]
    expect: dict = {}
    actions = []
    body = next((b for b in top if b["kind"] == "body" and not analysis["blocks"][b["path"]]["textLock"]), None)
    if body:
        body["text"] += "（已修改）"
        expect["edit"] = (body["id"], body["text"])
        actions.append("edit")
    heading = next((b for b in heads[1:] if not analysis["blocks"][b["path"]]["textLock"] and b["headingLevel"] < 6), None)
    if heading:
        heading["text"] += "甲"
        heading["headingLevel"] += 1
        expect["hlevel"] = (heading["id"], heading["text"], heading["headingLevel"])
        actions.append("hlevel")
    new = {"id": tw.new_block_id(), "path": None, "kind": "body", "text": "新增正文段落。", "sourceText": "", "headingLevel": None,
           "headingId": heads[0]["id"], "tableId": None, "protected": False, "isTable": False}
    blocks.insert(blocks.index(heads[0]) + 1, new)
    expect["insert"] = new["id"]
    actions.append("insert")
    removed = next((b for b in top if b["kind"] == "body" and b is not body and not analysis["blocks"][b["path"]]["deleteLock"]), None)
    if removed:
        blocks.remove(removed)
        expect["delete"] = removed["id"]
        actions.append("delete")
    for table in (b for b in top if b["isTable"] and analysis["tables"][b["path"]]["tableEditable"] in ("cells", "partial")):
        info = analysis["tables"][table["path"]]
        locked = {tuple(cell) for cell in info["lockedCells"]}
        free = [(r, c) for r, row in enumerate(info["cells"]) for c in range(len(row)) if (r, c) not in locked]
        if free:
            cells = copy.deepcopy(info["cells"])
            r, c = free[-1]
            cells[r][c] = (cells[r][c] + "改").strip()
            table["cells"] = cells
            expect["cell"] = (table["id"], cells)
            actions.append("cell")
            break
    level = heads[0]["headingLevel"]
    firsts = [b for b in blocks if not b["tableId"] and b["kind"] == "heading" and b["headingLevel"] == level]
    if len(firsts) >= 3:
        topb = [b for b in blocks if not b["tableId"]]
        a, b_ = topb.index(firsts[-2]), topb.index(firsts[-1])
        order = topb[:a] + topb[b_:] + topb[a:b_]
        children: dict[str, list] = {}
        for block in blocks:
            if block["tableId"]:
                children.setdefault(block["tableId"], []).append(block)
        draft["blocks"] = [x for block in order for x in [block] + children.get(block["id"], [])]
        expect["move"] = [b["id"] for b in order if not tw._is_virtual(b)]
        actions.append("move")
    styles = copy.deepcopy(analysis["styles"])
    styles["正文"]["size"] = 12
    return draft, styles, actions, expect


@pytest.mark.parametrize("source", TEMPLATES, ids=lambda p: p.name[:5])
def test_real_template_round_trip(source: Path, tmp_path: Path) -> None:
    content = source.read_bytes()
    analysis = tw._analysis(content)
    draft, styles, actions, expect = _edit(content)
    out = tmp_path / "v2.docx"
    ops = tw.apply_draft(source, draft, analysis["styles"], styles, out)
    assert ops > 0
    version = tw.build_version(out, draft, 2)
    by_id = {block["id"]: block for block in version["blocks"]}
    if "edit" in expect:
        assert by_id[expect["edit"][0]]["text"] == expect["edit"][1]
    if "hlevel" in expect:
        block = by_id[expect["hlevel"][0]]
        assert (block["text"], block["headingLevel"]) == expect["hlevel"][1:]
    assert by_id[expect["insert"]]["text"] == "新增正文段落。" and by_id[expect["insert"]]["path"]
    if "delete" in expect:
        assert expect["delete"] not in by_id
    if "cell" in expect:
        info = tw._analysis(out.read_bytes())["tables"][by_id[expect["cell"][0]]["path"]]
        assert info["cells"] == expect["cell"][1]
    if "move" in expect:
        assert [b["id"] for b in version["blocks"] if not b["tableId"] and not b.get("virtual")] == expect["move"]
    assert tw.read_styles(Document(out))["正文"]["size"] == 12
    # 版本 Word 可再次作为基底：未改动时零操作
    again = tmp_path / "v3.docx"
    assert tw.apply_draft(out, version, tw.read_styles(Document(out)), tw.read_styles(Document(out)), again) == 0
    # 新建文档：清掉指导后仍是合法 Word
    Document(BytesIO(tw.clean_version(out, version)))

