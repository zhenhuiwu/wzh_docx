"""F-H2 复现/回归：保存（与他人修改合并）在途时继续打字，不能与自己冲突、后打的字不能丢。

移植自测试工程师的 selfconf.py（只用本机 E2E_API/E2E_WEB 指定的服务）。环境变量：
  N（每种组合跑几次，默认 3）、GAPS（Ctrl+S 后到继续打字的间隔毫秒，逗号分隔，默认 0）、
  DELAY（逐字间隔，默认 60）、BIG（填充段落数，0 为 8 段小文档）、NOOWNER（不让张工改第 7 段）。
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
    return zhang.call("POST", f"/projects/{data['project']['id']}/documents",
                      json={"name": name, "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]})["id"]


def main() -> int:
    base.require_targets()
    data = base.seed()
    zhang = data["zhang"]
    n = int(os.environ.get("N", "3"))
    gaps = [int(item) for item in os.environ.get("GAPS", "0").split(",")]
    delay = int(os.environ.get("DELAY", "60"))
    big = int(os.environ.get("BIG", "0"))
    noowner = bool(os.environ.get("NOOWNER"))
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
                        "markdownContent": record["markdownContent"].replace("第7段：原始内容 7。", "第7段：原始内容 7（对方改）。")})
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
