"""测试工程师 table_other：表格不同行被双方修改按行合并；同一行仍是冲突。"""

from app.document_merge import merge3

TABLE = "| 列甲 | 列乙 |\n| --- | --- |\n| 行一甲 | 行一乙 |\n| 行二甲 | 行二乙 |\n"
BASE = "引段。\n\n" + TABLE + "\n尾段。\n"


def test_table_different_rows_merge_by_row():
    theirs = BASE.replace("行二乙", "行二乙（张改）")
    mine = BASE.replace("行一甲", "行一甲【P3】")
    result = merge3(BASE, theirs, mine)
    assert not result.conflicts
    assert "行一甲【P3】" in result.markdown and "行二乙（张改）" in result.markdown
    assert result.markdown.count("| 列甲 |") == 1


def test_table_same_row_still_conflicts():
    theirs = BASE.replace("行一乙", "行一乙（张改）")
    mine = BASE.replace("行一甲", "行一甲【P3】")
    result = merge3(BASE, theirs, mine)
    assert len(result.conflicts) == 1
    assert "行一乙（张改）" in result.markdown and "【P3】" not in result.markdown


def test_table_row_count_changed_still_conflicts():
    theirs = BASE.replace("| 行二甲 | 行二乙 |\n", "| 行二甲 | 行二乙 |\n| 行三甲 | 行三乙 |\n")
    mine = BASE.replace("行一甲", "行一甲【P3】")
    assert len(merge3(BASE, theirs, mine).conflicts) == 1
