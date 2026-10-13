"""F-H2 复现/回归：保存（与他人修改合并）在途时继续打字，不能与自己冲突、后打的字不能丢。

移植自测试工程师的 selfconf.py（只用本机 E2E_API/E2E_WEB 指定的服务）。环境变量：
  N（每种组合跑几次，默认 3）、GAPS（Ctrl+S 后到继续打字的间隔毫秒，逗号分隔，默认 0）、
  DELAY（逐字间隔，默认 60）、BIG（填充段落数，0 为 8 段小文档）、NOOWNER（不让张工改第 7 段）、
  SAME（测试工程师 F-H3/F-M3：张工改的是我正在改的第 3 段，即真冲突）。
SAME 的判定：张工的修改始终在服务器正文里（不被覆盖）；我打的完整标记在服务器正文、未处理冲突的“我的版本”或编辑区里
（不丢字）；若冲突卡称“对方的版本已在正文中”，正文里确实有对方的版本；冲突里的“对方”是张工（不与自己冲突）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from io import BytesIO

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_phase2 as base  # noqa: E402

PARAS = [f"第{i}段：原始内容 {i}。" for i in range(1, 9)]
MARK = "【丙0123456789ABCDEFGHIJ丁】"



def open_mode(api, doc: dict) -> dict:
    """A07 之前写的场景假定编写人员能改整篇：新建文档（默认按分工编辑）改为不限制分工（open）。"""
    view = api.call("GET", f"/documents/{doc['id']}/assignments")
    if view["assignmentMode"] != "open":
        api.call("PUT", f"/documents/{doc['id']}/assignment-mode",
                 json={"mode": "open", "expectedAssignmentsRevision": view["assignmentsRevision"]})
    return doc

def make_doc(data: dict, name: str, big: int) -> str:
    from docx import Document as WordDocument
    word = WordDocument()
    word.add_heading(name, 1)
    for text in PARAS + [f"填充段落{n}：这是一段用于放大文档体积的说明文字，内容与测试无关，重复若干次以模拟需求规格说明书的篇幅。" for n in range(big)]:
        word.add_paragraph(text)
    buffer = BytesIO()
    word.save(buffer)
    zhang = data["zhang"]
    upload = zhang.client.post("/files", files={"file": (f"{name}.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"purpose": "attachment", "projectId": data["project"]["id"]},
                               headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"selfconf-{uuid.uuid4()}"})
    assert upload.status_code == 201, upload.text
    return open_mode(zhang, zhang.call("POST", f"/projects/{data['project']['id']}/documents",
                      json={"name": name, "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]}))["id"]


def latest_mines(li, doc_id: str, timeout: float = 12.0) -> str:
    """方案 C：冲突后续写存在快照里，“我的版本”以 mineLatest 为准（审查员 M4 / 测试工程师）。等到标记完整或超时。"""
    deadline = time.time() + timeout
    while True:
        items = li.call("GET", f"/documents/{doc_id}/conflicts?status=open&actor=me")["items"]
        got = "\n".join(((item.get("mineLatest") or {}).get("markdown") or (item.get("mine") or {}).get("markdown") or "") for item in items)
        if MARK in got or not items or time.time() > deadline:
            return got
        time.sleep(0.5)


def same_row(page, doc_id: str, server: dict, editor: str, gap: int, i: int, big: int, li=None) -> dict:
    from app.db import connect
    with connect() as connection:
        rows = connection.execute("SELECT status, payload_json FROM document_conflicts WHERE document_id=%s", (doc_id,)).fetchall()
    items = [(row["status"], item) for row in rows for item in json.loads(row["payload_json"]).get("conflicts", [])]
    open_items = [item for status, item in items if status == "open"]
    md = server["markdownContent"]
    mines = latest_mines(li, doc_id) if li is not None else "\n".join(((item.get("mine") or {}).get("markdown") or "") for item in open_items)
    mine_open = MARK in mines
    # 冲突返回后才打的字会写在（已换成对方版本的）这一段末尾：标记分成前后两截——前一截在冲突副本（或正文），后一截在正文。
    # 只认已存到服务器的（正文或未处理冲突的“我的版本”），按顺序切成两截核对，不认只在编辑区里的字
    split_kept = any(MARK[:k] in mines + "\n" + md and MARK[k:] in md for k in range(1, len(MARK)))
    row = {"same": True, "gap": gap, "i": i, "big": big, "other_kept": "（对方改同段）" in md, "server_full": MARK in md,
           "mine_kept": MARK in md or mine_open or split_kept, "mine_whole": MARK in md or mine_open, "editor_full": MARK in editor, "conflicts": [(status, ((item.get("theirs") or {}).get("updatedBy") or {}).get("name"))
                                                                              for status, item in items],
           "status": page.get_by_test_id("save-status").inner_text()[:30], "rev": server["revision"]}
    row["theirs_by_other"] = all(name == "张工" for _, name in row["conflicts"])
    # 冲突卡的“已在正文中”必须属实：有未处理冲突时，对方版本在正文里
    row["card_truthful"] = not open_items or all(((item.get("theirs") or {}).get("markdown") or "") in md for item in open_items)
    row["ok"] = row["other_kept"] and row["mine_kept"] and row["theirs_by_other"] and row["card_truthful"]
    return row


def main() -> int:
    base.require_targets()
    data = base.seed()
    zhang = data["zhang"]
    li = base.Api(f"e2e-li-{base.RUN}")
    n = int(os.environ.get("N", "3"))
    gaps = [int(item) for item in os.environ.get("GAPS", "0").split(",")]
    delay = int(os.environ.get("DELAY", "60"))
    big = int(os.environ.get("BIG", "0"))
    noowner = bool(os.environ.get("NOOWNER"))
    same = bool(os.environ.get("SAME"))
    target = ("第3段：原始内容 3。", "第3段：原始内容 3（对方改同段）。") if same else ("第7段：原始内容 7。", "第7段：原始内容 7（对方改）。")
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
        page = ctx.new_page()
        base.login(page, f"e2e-li-{base.RUN}")
        for gap in gaps:
            for i in range(n):
                doc_id = make_doc(data, f"自冲突{'大' if big else ''}{gap}-{i}-{time.strftime('%H%M%S')}", big)
                page.goto(f"{base.WEB}/projects/{data['project']['id']}/documents/{doc_id}")
                expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
                expect(page.get_by_test_id("save-status")).to_contain_text("保存", timeout=60000)
                base.type_after(page, "第3段", MARK[:5])
                if not noowner:
                    record = zhang.call("GET", f"/documents/{doc_id}")
                    zhang.call("PATCH", f"/documents/{doc_id}/content", json={
                        "expectedRevision": record["revision"], "allowMerge": True, "clientSaveKey": str(uuid.uuid4()),
                        "markdownContent": record["markdownContent"].replace(*target)})
                page.keyboard.press("Control+s")
                page.wait_for_timeout(gap)
                page.keyboard.type(MARK[5:], delay=delay)
                page.wait_for_timeout(5000)
                try:
                    base.wait_saved(page, 30000)
                except Exception:  # noqa: BLE001 - 结果里看状态
                    pass
                server = zhang.call("GET", f"/documents/{doc_id}")
                editor = page.locator(".paper-content").inner_text()
                if same:
                    results.append(same_row(page, doc_id, server, editor, gap, i, big, li))
                    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                    continue
                row = {"gap": gap, "i": i, "big": big, "server_full": MARK in server["markdownContent"], "editor_full": MARK in editor,
                       "other_kept": noowner or "（对方改）" in server["markdownContent"],
                       "editor_other": noowner or "（对方改）" in editor,
                       "conflict": page.locator("text=已单独保存").count() > 0 or page.locator("text=冲突").count() > 0 and "冲突" in page.locator(".wb-notice").all_inner_texts().__str__(),
                       "status": page.get_by_test_id("save-status").inner_text()[:30], "rev": server["revision"]}
                row["ok"] = row["server_full"] and row["editor_full"] and row["other_kept"] and row["editor_other"] and not row["conflict"]
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        browser.close()
    passed = sum(row["ok"] for row in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
