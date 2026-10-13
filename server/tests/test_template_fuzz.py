"""A06 fuzz（17 §3.11）：20 份 438C 模板各做若干轮随机 1–20 步 L2 编辑（含故意碰锁定内容），走与 PUT draft + 发布相同的
validate_draft → apply_draft → build_version → clean_version 流程。不变式：要么往返一致、生成的 docx 能被 python-docx 打开，
要么得到带 message 的 TemplateEditError（接口上是 422，带 blockId），不出现其他异常（接口上的 500）。种子固定，可复现。"""

from __future__ import annotations

import copy
import random
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from app import template_merge as tm
from app import template_word as tw
from app.template_import import parse_template

TEMPLATES = sorted((Path(__file__).resolve().parents[2] / "438C-2021全套模板").glob("*.docx"))
SEED = 20261011
ROUNDS = 6  # 前一半随意，后一半只做界面允许的操作


def _free(block: dict, key: str) -> bool:
    return not (block.get("lock") or {}).get(key)


def _step(rng: random.Random, blocks: list[dict], safe: bool) -> None:
    """safe=False 随意动（可能碰锁）；safe=True 只动界面上允许动的内容（lock、lockedCells、tableEditable 预检后的操作）。"""
    top = [b for b in blocks if not b.get("tableId")]
    paras = [b for b in top if not b.get("isTable") and b["kind"] in {"body", "heading", "example", "placeholder"}]
    op = rng.choice(["text", "level", "insert_body", "insert_heading", "delete", "move", "cell", "guidance"])
    if op == "text" and paras:
        pool = [b for b in paras if _free(b, "text")] if safe else paras
        if pool:
            rng.choice(pool)["text"] += f"改{rng.randint(0, 99)}"
    elif op == "level":
        heads = [b for b in top if b["kind"] == "heading"]
        if heads:
            rng.choice(heads)["headingLevel"] = rng.randint(1, 6)
    elif op in {"insert_body", "insert_heading"} and top:
        anchor = rng.choice(top)
        index = blocks.index(anchor) + 1
        while index < len(blocks) and blocks[index].get("tableId"):
            index += 1
        heading = op == "insert_heading"
        blocks.insert(index, {"id": tw.new_block_id(), "path": None, "kind": "heading" if heading else "body",
                              "text": "随机新增标题" if heading else "随机新增正文。", "sourceText": "",
                              "headingLevel": rng.randint(1, 6) if heading else None, "headingId": None,
                              "tableId": None, "protected": False, "isTable": False})
    elif op == "delete" and paras:
        pool = [b for b in paras if _free(b, "delete")] if safe else paras  # 随意删可能碰到书签/域锁：应得到 TemplateEditError
        if pool:
            blocks.remove(rng.choice(pool))
    elif op == "move" and len(paras) >= 2:
        block = rng.choice(paras)
        blocks.remove(block)
        target = rng.choice([b for b in blocks if not b.get("tableId")])
        index = blocks.index(target) + 1
        while index < len(blocks) and blocks[index].get("tableId"):
            index += 1
        blocks.insert(index, block)
    elif op == "cell":
        tables = [b for b in top if b.get("isTable") and b.get("cells") and (not safe or b.get("tableEditable") in {"cells", "partial"})]
        if tables:
            table = rng.choice(tables)
            locked = {tuple(cell) for cell in table.get("lockedCells") or []} if safe else set()
            free = [(r, c) for r, row in enumerate(table["cells"]) for c in range(len(row)) if (r, c) not in locked]
            if free:
                r, c = rng.choice(free)
                table["cells"][r][c] = (table["cells"][r][c] + "改").strip()  # 随意时可能是锁定格
    elif op == "guidance":
        guides = [b for b in top if b["kind"] == "guidance"]
        if guides:
            rng.choice(guides)["text"] += "（随机修改）"


@pytest.mark.parametrize("source", TEMPLATES, ids=lambda p: p.name[:5])
def test_random_l2_edits_never_crash(source: Path, tmp_path: Path) -> None:
    content = source.read_bytes()
    structure = parse_template(content)
    structure["reviewed"] = True
    for block in structure["blocks"]:  # 模拟导入审核：受保护段落不能归为指导/示例/填写位置（template_import 同样拒绝）
        if block["protected"] and block["kind"] in {"guidance", "example", "placeholder"}:
            block["kind"] = "body"
    analysis = tw.analyze(source)
    client = tw.enrich(structure, analysis)
    rng = random.Random(f"{SEED}:{source.name}")
    outcomes = []
    for round_ in range(ROUNDS):
        draft = copy.deepcopy(client)
        for _ in range(rng.randint(1, 20)):
            _step(rng, draft["blocks"], safe=round_ >= ROUNDS // 2)
        try:
            blocks = tm.validate_draft(draft, structure, analysis)
            normalized = {"schemaVersion": 1, "revision": 1, "reviewed": True, "sourceHash": structure.get("sourceHash"), "blocks": blocks}
            out = tmp_path / f"r{round_}.docx"
            tw.apply_draft(source, normalized, analysis["styles"], analysis["styles"], out)
            version = tw.build_version(out, normalized, 2)
            Document(BytesIO(tw.clean_version(out, version)))
            Document(out)
            expected = [b["id"] for b in blocks if not b.get("tableId") and not tw._is_virtual(b)]
            assert [b["id"] for b in version["blocks"] if not b.get("tableId") and not b.get("virtual")] == expected
            outcomes.append("ok")
        except tw.TemplateEditError as error:
            assert error.details and all(isinstance(item.get("message"), str) and item["message"] for item in error.details)
            outcomes.append(error.code)
            if round_ >= ROUNDS // 2:
                # 只做界面允许的操作时，唯一允许的拒绝是“跨分节符移动”（界面只能整章上移/下移，随机移动比界面宽）
                assert all("分节符" in item["message"] for item in error.details), error.details
    assert len(outcomes) == ROUNDS
