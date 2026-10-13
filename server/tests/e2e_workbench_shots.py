"""工作台重塑改前/改后对比截图（1920×1080，另加 1366 一张）。

环境变量：E2E_API、E2E_WEB、DATABASE_URL 必须设置（同 e2e_phase2）；E2E_SHOTS 截图目录；E2E_PREFIX 文件名前缀（before / after）。
    E2E_API=http://127.0.0.1:8041/api/v1 E2E_WEB=http://127.0.0.1:5211 E2E_SHOTS=/workspace/wzh_collab/screens/workbench E2E_PREFIX=after \
    DATABASE_URL=sqlite:////tmp/wzh_phase2_run/live/app.db FILE_DATA_DIR=/tmp/wzh_phase2_run/live/files uv run python tests/e2e_workbench_shots.py
写作助手：本机没有配置 AI，脚本拦截 /capabilities 与草稿流接口，给出固定草稿（只用于看界面）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from io import BytesIO
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_phase2 as base  # noqa: E402

PREFIX = os.environ.get("E2E_PREFIX", "shot")
SHOTS = Path(os.environ.get("E2E_SHOTS", str(base.OUT / "workbench")))
FINAL = os.environ.get("E2E_FINAL") == "1"  # 终验补拍：全部 1920×1080，另加 F-M1/S-3/S-4/S-5 几张
WEB = base.WEB



def open_mode(api, doc: dict) -> dict:
    """A07 之前写的场景假定编写人员能改整篇：新建文档（默认按分工编辑）改为不限制分工（open）。"""
    view = api.call("GET", f"/documents/{doc['id']}/assignments")
    if view["assignmentMode"] != "open":
        api.call("PUT", f"/documents/{doc['id']}/assignment-mode",
                 json={"mode": "open", "expectedAssignmentsRevision": view["assignmentsRevision"]})
    return doc

def shot(page: Page, name: str, **kwargs):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / f"{PREFIX}-{name}.png"), **kwargs)
    print(f"shot {PREFIX}-{name}", flush=True)


def ai_routes(page: Page, revision_of):
    def capabilities(route):
        ok = {"available": True}
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"data": {
            "ai": {"writing": ok, "diagram": ok, "testExtraction": ok}, "wordPreview": {"available": False, "reason": "本机没有 Word"}, "visio": ok}}))
    page.route("**/api/v1/capabilities", capabilities)

    def draft(route):
        revision = revision_of()
        text = "软件应依据目标的雷达散射截面（RCS）起伏特性、速度、高度及航迹特征，对确认航迹进行目标类别识别，识别结果分为飞机、直升机、导弹、无人机及未知五类。\n\n识别结果随航迹报文输出，并给出置信度。"
        html = "".join(f"<p>{part}</p>" for part in text.split("\n\n"))
        lines = [
            {"type": "progress", "stage": "writing", "message": "正在编写", "content": "", "html": "", "revision": revision},
            {"type": "done", "content": text, "html": html, "revision": revision, "message": "", "allowedModes": ["append", "replace"]},
        ]
        route.fulfill(status=200, content_type="application/x-ndjson", body="\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n")
    page.route("**/sections/draft*", draft)


def outline_item(page: Page, text: str):
    return page.locator(".outline-item, .oitem", has_text=text).first


def main() -> int:
    base.require_targets()
    data = base.seed()
    doc_id = data["document"]["id"]
    zhang = data["zhang"]
    markdown = zhang.call("GET", f"/documents/{doc_id}")["markdownContent"]
    p_edit, p_conflict, p_dirty, p_draft = base.pick_paragraphs(markdown, 4)
    project_id = data["project"]["id"]

    # 整篇保存样例：嵌套列表（06：预览块与对象对不上）
    from docx import Document as WordDocument
    plain = WordDocument()
    plain.add_heading("概述", 1)
    plain.add_paragraph("本文档含嵌套列表，用来查看整篇保存标签。")
    buffer = BytesIO()
    plain.save(buffer)
    upload = zhang.client.post("/files", files={"file": ("整篇保存样例.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"purpose": "attachment", "projectId": project_id}, headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"wb-{uuid.uuid4()}"})
    whole = open_mode(zhang, zhang.call("POST", f"/projects/{project_id}/documents", json={"name": "整篇保存样例", "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]}))
    record = zhang.call("GET", f"/documents/{whole['id']}")
    content = record["markdownContent"].rstrip() + "\n\n- 一\n  - 一点一\n- 二\n"
    zhang.call("PATCH", f"/documents/{whole['id']}/content", json={"expectedRevision": record["revision"], "markdownContent": content})

    def revision():
        return zhang.call("GET", f"/documents/{doc_id}")["revision"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        def page_for(account: str, width: int = 1920, height: int = 1080):
            context = browser.new_context(viewport={"width": width, "height": height}, locale="zh-CN", accept_downloads=True)
            page = context.new_page()
            base.login(page, account)
            return page

        a = page_for(f"e2e-zhang-{base.RUN}")
        base.open_doc(a, data)
        a.wait_for_timeout(1500)
        target = base.paragraph(a, p_edit)
        target.scroll_into_view_if_needed()
        target.click()
        a.wait_for_timeout(300)
        shot(a, "01-普通编辑")
        if FINAL:
            # S-4：目录搜索（打开即聚焦）
            a.get_by_role("button", name="搜索标题").click()
            expect(a.locator(".osearch input")).to_be_focused(timeout=5000)
            a.keyboard.type("需求")
            a.wait_for_timeout(500)
            shot(a, "10-目录搜索")
            a.keyboard.press("Escape")
            expect(a.locator(".osearch")).to_have_count(0, timeout=5000)
            expect(a.get_by_role("button", name="搜索标题")).to_be_focused()

        # 保存状态各态
        base.type_after(a, p_dirty, "（未保存）")
        shot(a, "05a-保存状态-未保存")
        if FINAL:
            # S-5：下载菜单（打开后焦点在第一项，Esc 关闭并回到按钮）
            a.get_by_role("button", name="更多下载方式").click()
            expect(a.locator(".export-menu [role=menuitem]").first).to_be_focused(timeout=5000)
            a.wait_for_timeout(300)
            shot(a, "11-下载菜单")
            a.keyboard.press("Escape")
            expect(a.locator(".export-menu")).to_have_count(0, timeout=5000)
            expect(a.get_by_role("button", name="更多下载方式")).to_be_focused()
        held = []
        a.route("**/documents/*/content", lambda route: held.append(route) if route.request.method == "PATCH" else route.continue_())
        base.save_now(a)
        expect(a.get_by_test_id("save-status")).to_contain_text("保存中", timeout=10000)
        shot(a, "05b-保存状态-保存中")
        for route in held:
            route.continue_()
        a.unroute("**/documents/*/content")
        base.wait_saved(a)
        shot(a, "05c-保存状态-已保存")
        a.route("**/documents/*/content", lambda route: route.abort() if route.request.method == "PATCH" else route.continue_())
        base.type_after(a, p_dirty, "（重试）")
        base.save_now(a)
        expect(a.get_by_test_id("save-status")).to_contain_text(re.compile("重试"), timeout=30000)
        shot(a, "05d-保存状态-重试中")
        a.unroute("**/documents/*/content")
        a.evaluate("window.dispatchEvent(new Event('online'))")
        base.wait_saved(a, 60000)

        def reject(route):
            if route.request.method != "PATCH":
                return route.continue_()
            sent = json.loads(route.request.post_data or "{}").get("markdownContent", "")
            block_text = next((b for b in sent.split("\n\n") if "（会被拒）" in b), "")
            route.fulfill(status=422, content_type="application/json", body=json.dumps({"error": {
                "code": "WORD_BLOCK_PROTECTED", "message": "该段含 Word 域或目录，不能在这里修改。",
                "details": {"location": {"headingPath": ["范围"], "ordinal": 1, "kind": "paragraph"},
                            "reason": "该段含 Word 域或目录，不能在这里修改。", "blockText": block_text}}}))
        a.route("**/documents/*/content", reject)
        base.type_after(a, p_dirty, "（会被拒）")
        base.save_now(a)
        expect(a.get_by_test_id("save-blocked")).to_be_visible(timeout=15000)
        shot(a, "05e-保存状态-保存失败")
        a.unroute("**/documents/*/content")
        a.get_by_test_id("revert-block").click()
        expect(a.get_by_test_id("save-blocked")).to_have_count(0, timeout=20000)
        base.wait_saved(a)
        a.context.set_offline(True)
        a.evaluate("window.dispatchEvent(new Event('offline'))")
        base.type_after(a, p_dirty, "（离线）")
        base.save_now(a)
        expect(a.get_by_test_id("save-status")).to_contain_text(re.compile("离线|网络"), timeout=30000)
        shot(a, "05f-保存状态-离线")
        a.context.set_offline(False)
        a.evaluate("window.dispatchEvent(new Event('online'))")
        base.wait_saved(a, 60000)

        # 版本记录
        a.get_by_role("button", name="版本记录").click()
        expect(a.locator(".history-panel")).to_be_visible(timeout=15000)
        try:
            expect(a.locator(".history-panel .skel")).to_have_count(0, timeout=30000)
        except AssertionError:
            pass
        a.wait_for_timeout(1500)
        shot(a, "03-版本记录")
        if a.locator(".history-panel .vitem").count():  # S12：重塑后才有
            a.locator(".history-panel .vitem").first.click()
            expect(a.locator(".history-panel .skel")).to_have_count(0, timeout=30000)
            compare = a.locator(".history-panel").get_by_role("button", name="与上一版本比较")
            compare.click(timeout=10000)
            expect(a.locator(".history-panel .skel")).to_have_count(0, timeout=30000)
            a.wait_for_timeout(1200)
            shot(a, "03b-版本记录-与上一版本比较")
        a.close()

        # 写作助手（拦截能力与草稿流）
        w = page_for(f"e2e-zhang-{base.RUN}")
        ai_routes(w, revision)
        base.open_doc(w, data)
        w.wait_for_timeout(800)
        outline_item(w, "需求").click() if outline_item(w, "需求").count() else None
        w.get_by_role("button", name="写作助手").first.click() if not w.locator(".ai-panel").count() else None
        expect(w.locator(".ai-panel")).to_be_visible(timeout=10000)
        w.locator("#ai-instruction").fill("识别类别按总体要求的五类；写明识别依据与输出方式。")
        w.locator(".ai-panel").get_by_role("button", name=re.compile("生成草稿")).click()
        expect(w.locator("[data-ai-preview]")).to_be_visible(timeout=20000)
        w.wait_for_timeout(800)
        shot(w, "02-写作助手")
        w.close()

        # 冲突面板（李工手动保存遇到同段冲突）
        b = page_for(f"e2e-li-{base.RUN}")
        base.open_doc(b, data)
        base.type_after(b, p_conflict, "（李工的写法）")
        rec = zhang.call("GET", f"/documents/{doc_id}")
        blocks = rec["markdownContent"].split("\n\n")
        index = next(i for i, block in enumerate(blocks) if block.strip().startswith(p_conflict))
        blocks[index] = blocks[index].rstrip() + "（张工的写法）"
        zhang.call("PATCH", f"/documents/{doc_id}/content", json={"expectedRevision": rec["revision"], "markdownContent": "\n\n".join(blocks),
                                                                  "allowMerge": True, "clientSaveKey": str(uuid.uuid4())})
        base.save_now(b)
        panel = b.locator(".conflict-panel")
        expect(panel.locator(".ccard")).to_have_count(1, timeout=20000)
        base.wait_saved(b)
        b.wait_for_timeout(1000)
        panel.get_by_test_id("conflict-locate").click()
        b.wait_for_timeout(1200)
        shot(b, "04-冲突面板")
        # 他人更新提示条（本地有修改）
        b.get_by_test_id("conflict-toggle").click()
        base.type_after(b, p_edit, "（李工未保存）")
        rec = zhang.call("GET", f"/documents/{doc_id}")
        blocks = rec["markdownContent"].split("\n\n")
        index = next(i for i, block in enumerate(blocks) if block.strip().startswith(p_draft))
        blocks[index] = blocks[index].rstrip() + "（张工更新）"
        zhang.call("PATCH", f"/documents/{doc_id}/content", json={"expectedRevision": rec["revision"], "markdownContent": "\n\n".join(blocks),
                                                                  "allowMerge": True, "clientSaveKey": str(uuid.uuid4())})
        b.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(b.get_by_test_id("others-updated")).to_be_visible(timeout=10000)
        shot(b, "08-他人更新提示条")
        base.wait_saved(b, 30000)
        if FINAL:
            # F-M1：载入他人更新时的“正在载入 {姓名} 的更新…”（版式请求放慢 4 秒以便截图）
            b.wait_for_timeout(2500)
            b.evaluate("""() => { const original = window.fetch; window.__origFetch = original;
                window.fetch = (input, init) => String(input instanceof Request ? input.url : input).includes('/preview')
                    ? new Promise(resolve => setTimeout(resolve, 4000)).then(() => original(input, init)) : original(input, init); }""")
            rec = zhang.call("GET", f"/documents/{doc_id}")
            blocks = rec["markdownContent"].split("\n\n")
            index = next(i for i, block in enumerate(blocks) if block.strip().startswith(p_draft))
            blocks[index] = blocks[index].rstrip() + "（张工又更新）"
            zhang.call("PATCH", f"/documents/{doc_id}/content", json={"expectedRevision": rec["revision"], "markdownContent": "\n\n".join(blocks),
                                                                      "allowMerge": True, "clientSaveKey": str(uuid.uuid4())})
            b.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(b.get_by_test_id("editor-refreshing")).to_be_visible(timeout=15000)
            shot(b, "12-载入他人更新提示")
            expect(b.get_by_test_id("editor-refreshing")).to_have_count(0, timeout=30000)
            b.evaluate("() => { window.fetch = window.__origFetch; }")

        # 草稿恢复条
        b.context.set_offline(True)
        base.type_after(b, p_draft, "（断网关页）")
        b.wait_for_timeout(600)
        ctx = b.context
        b.close()
        ctx.set_offline(False)
        r = ctx.new_page()
        base.open_doc(r, data)
        expect(r.get_by_test_id("draft-recovery")).to_be_visible(timeout=20000)
        shot(r, "06-草稿恢复条")
        r.get_by_test_id("draft-recovery").get_by_role("button", name="恢复并保存").click()
        expect(r.get_by_test_id("draft-recovery")).to_have_count(0, timeout=30000)
        r.close()

        # 只读
        v = page_for(f"e2e-wang-{base.RUN}")
        v.goto(f"{WEB}/projects/{project_id}/documents/{doc_id}")
        expect(v.locator(".paper-content")).to_be_visible(timeout=60000)
        expect(v.get_by_test_id("save-status")).to_contain_text("只读", timeout=15000)
        shot(v, "05g-保存状态-只读")
        v.close()

        # 整篇保存标签
        s = page_for(f"e2e-zhang-{base.RUN}")
        s.goto(f"{WEB}/projects/{project_id}/documents/{whole['id']}")
        expect(s.locator(".paper-content")).to_be_visible(timeout=60000)
        s.wait_for_timeout(2500)
        tag = s.get_by_test_id("whole-save")
        if tag.count():
            tag.hover()
            s.wait_for_timeout(600)
        print("whole-save tag count:", tag.count(), flush=True)
        shot(s, "07-整篇保存")
        s.close()

        # 1366：写作助手面板
        n = page_for(f"e2e-zhang-{base.RUN}", *((1920, 1080) if FINAL else (1366, 768)))
        ai_routes(n, revision)
        base.open_doc(n, data)
        n.wait_for_timeout(800)
        n.get_by_role("button", name="版本记录").click()
        expect(n.locator(".history-panel .skel")).to_have_count(0, timeout=30000)
        n.wait_for_timeout(1500)
        shot(n, "09-版本记录" if FINAL else "09-1366-版本记录")
        n.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
