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
