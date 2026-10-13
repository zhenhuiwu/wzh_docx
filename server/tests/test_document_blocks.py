"""S4：块切分往返与规范键（01 §8.2 用例 1–3）。diff3（用例 4）随 S5 加入。"""

from pathlib import Path

import pytest

from app.markdown_blocks import assemble, canonical_key, heading_text, split

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "438C-2021全套模板").glob("*.docx"))

CASES = [
    "",
    "\n\n",
    "# 标题\n\n正文。\n",
    "## 2 引用文件\n\n- 紧凑一\n- 紧凑二\n\n- 松散一\n\n- 松散二\n",
    "- a\n\n  续行段落\n\n- b\n  - 嵌套\n    - 更深\n\n之后的段落",
    "1. 第一\n2. 第二\n   续行\n\n普通段",
    "| 列一 | 列二 |\n| --- | --- |\n| 1 | 2 |\n\n表 1 说明\n",
    "```python\ncode\n\n# not heading\n```\n\nx",
    "![](docx-image:img1)\n\n> 引用\n> 第二行\n\nSetext\n===\n",
    "中文 English 😀 混排。\n\n同样的段落。\n\n同样的段落。\n\n同样的段落。\n",
    "  首部空白\n\n\n尾部空白  \n\n\n",
    "a\r\nb\r\n\r\n- x\r\n- y\r\n\r\n# H\r\n",
    "x\n```\n未闭合围栏",
    "<div>html</div>\n\n    缩进代码\n\n正文",
]


@pytest.mark.parametrize("text", CASES)
def test_split_round_trips_byte_for_byte(text):
    prefix, blocks = split(text)
    assert assemble(prefix, blocks) == text
    assert all(block.body for block in blocks)


def test_block_kinds():
    _, blocks = split(CASES[4] + "\n\n" + CASES[6] + "\n" + CASES[8])
    kinds = [block.kind for block in blocks]
    assert kinds[:2] == ["list_item", "list_item"]
    assert "table" in kinds and "image" in kinds
    assert blocks[0].body.startswith("- a") and "续行段落" in blocks[0].body
    assert "嵌套" in blocks[1].body           # 嵌套列表属于父项
    _, headings = split("### 3.2 软件能力需求 ###\n")
    assert headings[0].kind == "heading" and headings[0].heading_level == 3
    assert heading_text(headings[0].body) == "3.2 软件能力需求"


@pytest.mark.skipif(not TEMPLATES, reason="仓库没有 438C 模板目录")
def test_438c_templates_round_trip():
    from app.docx_parser import parse_docx

    assert len(TEMPLATES) == 20
    for path in TEMPLATES:
        text = parse_docx(str(path)).markdown
        prefix, blocks = split(text)
        assert assemble(prefix, blocks) == text, path.name
        assert any(block.kind == "heading" for block in blocks), path.name


@pytest.mark.parametrize(("before", "after"), [
    ("* 甲\n", "- 甲\n"),
    ("产品型号\\-XXXX", "产品型号-XXXX"),
    ("正文。  ", "正文。"),
])
def test_canonical_key_ignores_dialect(before, after):
    assert canonical_key(before) == canonical_key(after)


@pytest.mark.parametrize(("before", "after"), [
    ("正文。", "正文！"),
    ("**粗体**正文", "**粗体正**文"),
    ("![](docx-image:a)", "![](docx-image:b)"),
])
def test_canonical_key_detects_changes(before, after):
    assert canonical_key(before) != canonical_key(after)


# 用例 4：diff3（01 §5.2 整篇规则）
from app.document_merge import merge3  # noqa: E402

BASE = "# H\n\nP1\n\nP2\n\nP3\n"


def _merge(theirs, mine, **kwargs):
    result = merge3(BASE, theirs, mine, **kwargs)
    return result.markdown, [conflict.reason for conflict in result.conflicts]


def test_diff3_rules():
    assert _merge(BASE.replace("P1", "T1"), BASE.replace("P3", "M3")) == ("# H\n\nT1\n\nP2\n\nM3\n", [])
    assert _merge(BASE.replace("P2", "T2"), BASE.replace("P2", "M2"))[1] == ["MODIFIED"]
    assert _merge(BASE.replace("P2", "X"), BASE.replace("P2", "X")) == ("# H\n\nP1\n\nX\n\nP3\n", [])
    both = _merge(BASE.replace("P2\n\n", "P2\n\nTI\n\n"), BASE.replace("P2\n\n", "P2\n\nMI\n\n"))
    assert both == ("# H\n\nP1\n\nP2\n\nTI\n\nMI\n\nP3\n", [])          # 都保留、对方在前
    assert _merge(BASE.replace("P2", "T2"), BASE.replace("P2\n\n", ""))[1] == ["DELETED"]   # 删 vs 改
    assert _merge(BASE.replace("P2\n\n", ""), BASE.replace("P2", "M2"))[1] == ["DELETED"]   # 改 vs 删
    assert _merge(BASE.replace("P2\n\n", ""), BASE.replace("P2\n\n", "")) == ("# H\n\nP1\n\nP3\n", [])
    assert _merge(BASE.replace("P1", "T1"), BASE.replace("P2", "M2")) == ("# H\n\nT1\n\nM2\n\nP3\n", [])   # 相邻各改一处
    assert _merge(BASE.replace("P2", "T2"), BASE.replace("P2", "P2  ")) == ("# H\n\nP1\n\nT2\n\nP3\n", [])  # 只是写法差异


def test_diff3_duplicate_paragraphs_and_insert_dedupe():
    base = "X\n\nX\n\nX\n"
    result = merge3(base, base + "\nT\n", "X\n\nY\n\nX\n")
    assert result.conflicts or result.markdown == "X\n\nY\n\nX\n\nT\n"     # ∈ {正确, 冲突}
    same = BASE.replace("P2\n\n", "P2\n\nS\n\n")
    assert merge3(BASE, same, same, dedupe_inserts=True).markdown.count("S\n") == 1     # 同一账号重发：去重
    assert merge3(BASE, same, same, dedupe_inserts=False).markdown.count("S\n") == 2    # 不同账号：都保留
    assert merge3("- a\n- b\n", "- a\n- b\n- t\n", "- a\n- m\n- b\n").markdown == "- a\n- m\n- b\n- t\n"


def test_whitespace_only_edits_survive_concurrent_merge():
    """终验 S-1：只改空白的修改在并发合并时不能被静默丢掉。"""
    base = "# 标题\n\n导出下载   的完整说明。\n\n另一段。\n"
    theirs = base.replace("另一段。", "对方改了另一段。")
    mine = base.replace("下载   的", "下载  的")
    # 对方没碰这一块：采用我方原文
    result = merge3(base, theirs, mine)
    assert not result.conflicts and result.whitespace_dropped == 0
    assert "下载  的完整" in result.markdown and "对方改了另一段。" in result.markdown
    # 全角空格同理
    full = merge3(base, theirs, base.replace("下载   的", "下载　　的"))
    assert "下载　　的" in full.markdown
    # 双方都只改了空白、改法不同：保留对方，计入提示
    both = merge3(base, base.replace("下载   的", "下载 的"), mine)
    assert not both.conflicts and "下载 的完整" in both.markdown and both.whitespace_dropped == 1
    # 对方改了文字、我只改空白：保留对方的文字，同样提示
    real = merge3(base, base.replace("下载   的完整", "下载   的全部"), mine)
    assert not real.conflicts and "的全部" in real.markdown and real.whitespace_dropped == 1
    # 双方改动相同（含空白）：不提示
    same = merge3(base, mine.replace("另一段。", "对方。"), mine)
    assert same.whitespace_dropped == 0 and "下载  的" in same.markdown


def test_whitespace_merge_pairs_by_node_kind():
    """配对比较带上节点类型：段落改成同文字的标题不算“只改空白”。"""
    base = "引言\n\n正文。\n"
    result = merge3(base, base.replace("正文。", "正文改。"), "# 引言\n\n正文。\n")
    assert "# 引言" in result.markdown and result.whitespace_dropped == 0


def test_whitespace_notes_for_deleted_and_overridden():
    """终审建议：对方删段 / 对方只改空白而我改文字，分别给出准确的提示计数。"""
    base = "# 标题\n\n导出下载   的完整说明。\n\n另一段。\n"
    mine_ws = base.replace("下载   的", "下载  的")
    deleted = merge3(base, base.replace("导出下载   的完整说明。\n\n", ""), mine_ws)
    assert not deleted.conflicts and "导出下载" not in deleted.markdown
    assert deleted.whitespace_deleted == 1 and deleted.whitespace_dropped == 0
    assert deleted.whitespace_notes() == {"whitespaceDeleted": 1}
    overridden = merge3(base, base.replace("下载   的", "下载 的"), base.replace("完整说明", "全部说明"))
    assert not overridden.conflicts and "下载   的全部说明" in overridden.markdown
    assert overridden.whitespace_notes() == {"theirsWhitespaceOverridden": 1}


def test_code_block_indentation_is_a_real_change():
    """终审建议：代码块缩进有语义——双方把同一代码块的缩进改得不同要进冲突；只一方改则采用其修改。"""
    base = "# 示例\n\n```\nif a:\n    run()\n```\n\n说明。\n"
    theirs = base.replace("    run()", "  run()")
    mine = base.replace("    run()", "\trun()")
    both = merge3(base, theirs, mine)
    assert len(both.conflicts) == 1 and both.conflicts[0].reason == "MODIFIED"
    assert both.whitespace_notes() == {}
    only_mine = merge3(base, base.replace("说明。", "对方说明。"), mine)
    assert not only_mine.conflicts and "\trun()" in only_mine.markdown and "对方说明。" in only_mine.markdown
