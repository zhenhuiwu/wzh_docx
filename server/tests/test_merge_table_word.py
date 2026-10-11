"""设计师复验 2 C：按行合并出的表通不过 Word 校验（含合并单元格的表）时不退为整篇 FORMAT 冲突。"""

from app.errors import AppError
from tests.test_document_access import _as, members  # noqa: F401
from tests.test_document_concurrency import current, pair, save, team  # noqa: F401
from tests.test_documents import document_context  # noqa: F401

TABLE = "| 分组 | 名称 | 值 |\n| --- | --- | --- |\n| 甲组 | 行一 | 一 |\n| 甲组 | 行二 | 二 |\n"
DOC = "引段。\n\n" + TABLE + "\n尾段。\n"


def _fake_word(rule):
    def check(connection, row, content, **kwargs):
        if rule(content):
            raise AppError(422, "WORD_BLOCK_PROTECTED", "不能改写合并单元格，请保留原表头和表格结构。")
    return check


def _start(client, did):
    assert save(client, did, 1, DOC, _as(client, "editor")).status_code == 200
    return current(client, did)["revision"]


def test_row_merge_failing_word_becomes_table_conflict_not_whole_document(team, monkeypatch):
    client, did, _ = team
    rev = _start(client, did)
    # 两人各改合并区里的一行，各自都合法，合在一起 Word 写不回（模拟合并单元格不一致）
    monkeypatch.setattr("app.documents._check_word", _fake_word(lambda md: "（张改）" in md and "（李改）" in md))
    assert save(client, did, rev, DOC.replace("| 一 |", "| 一（张改） |"), _as(client, "editor")).status_code == 200
    mine = DOC.replace("| 二 |", "| 二（李改） |").replace("尾段。", "尾段【李工别处的修改】。")
    response = save(client, did, rev, mine, _as(client, "editor2"))
    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    reasons = [item["reason"] for item in details["conflicts"]]
    assert reasons == ["MODIFIED"], reasons  # 整表冲突，不是整篇 FORMAT
    assert "| 分组 |" in details["conflicts"][0]["mine"]["markdown"] and "引段" not in details["conflicts"][0]["mine"]["markdown"]
    assert "【李工别处的修改】" in details["appliableMarkdown"]  # 别处的修改仍可进正文


def test_own_invalid_word_edit_returns_422_not_format_conflict(team, monkeypatch):
    client, did, _ = team
    rev = _start(client, did)
    monkeypatch.setattr("app.documents._check_word", _fake_word(lambda md: "甲组（李改）" in md))
    assert save(client, did, rev, DOC.replace("引段。", "引段（张改）。"), _as(client, "editor")).status_code == 200
    mine = DOC.replace("| 甲组 | 行二 |", "| 甲组（李改） | 行二 |").replace("尾段。", "尾段【李工别处的修改】。")
    response = save(client, did, rev, mine, _as(client, "editor2"))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "WORD_BLOCK_PROTECTED"
    assert "引段（张改）。" in current(client, did)["markdownContent"]
