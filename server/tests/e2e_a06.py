"""A06 模板完整编辑与版本快照浏览器验收（16 §3.8，1920×1080）。

环境变量：E2E_API、E2E_WEB、DATABASE_URL、FILE_DATA_DIR **必须设置**（脚本直接在被测库里建账号）；
E2E_SHOTS（截图目录）、E2E_OUT（失败截图）、E2E_VIEWPORT（默认 1920x1080，1366 验收用 1366x768）。

运行（先起服务，例如 8061/5231）：
    E2E_API=http://127.0.0.1:8061/api/v1 E2E_WEB=http://127.0.0.1:5231 E2E_SHOTS=/tmp/wzh_a06_run/shots \\
    DATABASE_URL=sqlite:////tmp/wzh_a06_run/live/app.db FILE_DATA_DIR=/tmp/wzh_a06_run/live/files \\
        .venv/bin/python tests/e2e_a06.py
覆盖：T1 模板文档表的版本列；T2 在编辑器里改标题文字与级别、改正文、新增一章、删除一章、移动一章、改单元格、改指导（A6-1）；
T3 改“正文”字号，300ms 防抖后显示影响段数，与 format-impact 响应一致（A6-14）；T4 发布弹窗摘要与已有文档提示，发布第 2 版（A6-1/A6-7）；
T5 对比第 1 版与第 2 版（A6-3）；T6 用新版本新建文档、老文档仍是第 1 版并提示有新版本（A6-2/A6-7）；T7 三态表格与目录锁（A6-8/9/10）；
T8 上传新 Word 作为草稿，未落位指导处理完才能发布（A6-9）；T9 并发保存 409 提示条与下载（A6-6）；
T10 草稿过期 → 合并到最新版本，冲突逐处选择后合并、再发布（A6-13）；T11 恢复第 1 版为草稿并发布（A6-4）；
T12 项目创建者只读（A6-5）；T13 审计员 403（A6-12）；T14 修复轮（弹窗焦点、放弃草稿、草稿被别人发布后继续编辑走过期合并）。admin/admin123 登录、不改密（用户决定）。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("E2E_API", "")
WEB = os.environ.get("E2E_WEB", "")
OUT = Path(os.environ.get("E2E_OUT", str(Path(tempfile.gettempdir()) / "e2e_a06")))
SHOTS = Path(os.environ.get("E2E_SHOTS", str(OUT / "shots")))
PASSWORD = "E2e-a06-pass-1"
ADMIN, ADMIN_PASSWORD = "admin", "admin123"
RUN = uuid.uuid4().hex[:6]
_W, _H = (int(n) for n in os.environ.get("E2E_VIEWPORT", "1920x1080").lower().split("x"))
VIEWPORT = {"width": _W, "height": _H}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SRS = next((ROOT / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
results: list[tuple[str, bool, str]] = []
open_pages: list[Page] = []


def step(name: str):
    def wrap(fn):
        def run(*args, **kwargs):
            if os.environ.get("E2E_ONLY") and os.environ["E2E_ONLY"] not in name:
                print(f"SKIP {name}", flush=True)
                return
            try:
                fn(*args, **kwargs)
                results.append((name, True, ""))
                print(f"PASS {name}", flush=True)
            except Exception as error:  # noqa: BLE001 - 汇总后统一失败
                results.append((name, False, f"{type(error).__name__}: {error}"))
                print(f"FAIL {name}: {error}", flush=True)
                OUT.mkdir(parents=True, exist_ok=True)
                for index, page in enumerate(open_pages):
                    if not page.is_closed():
                        page.screenshot(path=str(OUT / f"fail-{len(results)}-{index}.png"))
                if os.environ.get("E2E_STOP_ON_FAIL") == "1":
                    raise
        return run
    return wrap


class Api:
    def __init__(self, account: str, password: str = PASSWORD):
        self.client = httpx.Client(base_url=API, timeout=180)
        csrf = self.client.get("/auth/csrf").json()["data"]["csrfToken"]
        response = self.client.post("/auth/login", json={"account": account, "password": password}, headers={"X-CSRF-Token": csrf})
        response.raise_for_status()
        self.csrf = response.json()["data"]["csrfToken"]

    def raw(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"X-CSRF-Token": self.csrf, **kwargs.pop("headers", {})}
        return self.client.request(method, path, headers=headers, **kwargs)

    def call(self, method: str, path: str, **kwargs):
        response = self.raw(method, path, **kwargs)
        if response.status_code >= 400:
            raise AssertionError(f"{method} {path} → {response.status_code} {response.text[:300]}")
        return response.json()["data"] if response.content else None

    def upload(self, name: str, content: bytes) -> str:
        response = self.raw("POST", "/files", files={"file": (name, content, DOCX)}, data={"purpose": "template"},
                            headers={"Idempotency-Key": f"a06-{uuid.uuid4().hex}"})
        assert response.status_code == 201, response.text
        return response.json()["data"]["id"]


def seed() -> dict:
    sys.path.insert(0, str(ROOT / "server"))
    from app.db import connect
    from app.identity import create_user
    users = {}
    with connect() as connection:
        row = connection.execute("select id from users where account_key = %s", (ADMIN,)).fetchone()
        users["admin"] = row[0] if row else create_user(account=ADMIN, name="系统管理员", password=ADMIN_PASSWORD, roles=["system_admin"], connection=connection)
        for key, name, roles in (("maint", "模板维护甲", ["template_manager"]), ("creator", "项目创建者乙", ["project_creator"]),
                                 ("auditor", "审计员丙", ["auditor"])):
            users[key] = create_user(account=f"{key}{RUN}", name=name, password=PASSWORD, roles=roles, connection=connection)
        connection.commit()
    admin = Api(ADMIN, ADMIN_PASSWORD)
    file_id = admin.upload(SRS.name, SRS.read_bytes())
    group = admin.call("POST", "/templates", json={"name": f"GJB 438C-2021 {RUN}", "category": "438C"})
    structure = admin.call("POST", "/template-documents/parse", json={"wordFileId": file_id})
    structure["reviewed"] = True
    td = admin.call("POST", f"/templates/{group['id']}/documents", json={"name": "软件需求规格说明", "wordFileId": file_id, "importStructure": structure})
    project = admin.call("POST", "/projects", json={"name": f"综合保障信息系统 {RUN}", "ownerId": str(users["admin"]), "status": "active"},
                         headers={"Idempotency-Key": f"a06-{uuid.uuid4().hex}"})
    old_doc = admin.call("POST", f"/projects/{project['id']}/documents", json={"name": "需求规格（第 1 版建）", "sourceType": "template", "templateDocumentId": td["id"]})
    # A05 起 PUT 只改已有成员，新增成员走 members:batch
    admin.call("POST", f"/projects/{project['id']}/members:batch",
               json={"userIds": [str(users['auditor'])], "role": "viewer",
                     "expectedMembersRevision": admin.call("GET", f"/projects/{project['id']}/members")["membersRevision"]})
    return {"users": users, "admin": admin, "group": group, "td": td, "project": project, "old_doc": old_doc}


def login(page: Page, account: str, password: str = PASSWORD):
    page.context.clear_cookies()
    page.goto(f"{WEB}/login")
    page.fill("#staff-id", account)
    page.fill("#password", password)
    page.locator("form button[type=submit]").click()
    page.wait_for_url(re.compile(r"/projects"), timeout=30000)


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def require_targets() -> None:
    missing = [name for name, value in (("E2E_API", API), ("E2E_WEB", WEB), ("DATABASE_URL", os.environ.get("DATABASE_URL", "")),
                                        ("FILE_DATA_DIR", os.environ.get("FILE_DATA_DIR", ""))) if not value]
    if missing:
        raise SystemExit(f"请设置环境变量 {', '.join(missing)}（被测服务的接口、前端地址和同一个数据库），脚本不使用默认值。")


def top(blocks: list[dict]) -> list[dict]:
    return [block for block in blocks if not block.get("tableId")]


def chapter(blocks: list[dict], heading_id: str) -> list[dict]:
    items = top(blocks)
    start = next(i for i, block in enumerate(items) if block["id"] == heading_id)
    level = items[start]["headingLevel"]
    end = start + 1
    while end < len(items) and not (items[end]["kind"] == "heading" and items[end]["headingLevel"] <= level):
        end += 1
    return items[start:end]


def block(page: Page, block_id: str):
    return page.locator(f'.blk[data-block-id="{block_id}"]')


def saved(page: Page):
    expect(page.get_by_test_id("save-status")).to_contain_text("草稿已保存", timeout=30000)


def select(page: Page, block_id: str):
    target = block(page, block_id)
    target.scroll_into_view_if_needed()
    target.click(position={"x": 6, "y": 6})
    expect(target).to_have_class(re.compile(r"\bon\b"))


def edit_text(page: Page, block_id: str, text: str):
    select(page, block_id)
    page.get_by_test_id("tpl-edit-text").fill(text)


def main() -> int:  # noqa: C901 - 验收脚本按用例顺序展开
    require_targets()
    data = seed()
    admin, td, pid = data["admin"], data["td"]["id"], data["project"]["id"]
    gid = data["group"]["id"]
    editor_url = f"{WEB}/system/templates/{gid}/documents/{td}/edit"
    base = admin.call("GET", f"/template-documents/{td}/draft")["base"]["structure"]["blocks"]
    state: dict = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ctx = browser.new_context(viewport=VIEWPORT, accept_downloads=True)
        page = ctx.new_page()
        open_pages.append(page)
        login(page, ADMIN, ADMIN_PASSWORD)

        @step("T1 模板文档表：当前版本 / 草稿 / 使用中 / 编辑模板")
        def t1():
            page.goto(f"{WEB}/system/templates/{gid}")
            row = page.get_by_test_id("tpl-doc-row")
            expect(row.get_by_test_id("tpl-doc-version")).to_contain_text("第 1 版")
            expect(row).to_contain_text("1 篇")
            expect(row).not_to_contain_text("核对模板")
            shot(page, "A06-01-模板文档表")
            row.get_by_test_id("tpl-doc-edit").click()
            page.wait_for_url(re.compile(r"/edit$"))
            expect(page.locator(".paper .blk").first).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("当前第 1 版")
            shot(page, "A06-02-编辑器")
        t1()

        @step("T2 A6-1 在编辑器里改标题文字和级别、正文、指导、单元格，新增一章、删除一章、移动一章")
        def t2():
            items = top(base)
            heads = [item for item in items if item["kind"] == "heading"]
            body = next(item for item in items if item["kind"] == "body" and item["path"] and not item["lock"]["text"])
            guidance = next(item for item in items if item["kind"] == "guidance" and item["path"])
            level2 = [item for item in heads if item["headingLevel"] == 2]
            renamed = level2[1]
            level1 = [item for item in heads if item["headingLevel"] == 1]
            moved = level1[-2]
            deletable = next(item for item in reversed(heads) if item["headingLevel"] >= 2 and item["id"] not in {renamed["id"], moved["id"]}
                             and not any(child["isTable"] or child["kind"] == "toc" or child["lock"]["delete"] for child in chapter(base, item["id"]))
                             and item["id"] not in {child["id"] for child in chapter(base, moved["id"])})
            table = next(item for item in items if item.get("isTable") and item.get("tableEditable") == "cells")
            state.update(body=body["id"], renamed=renamed["id"], moved=moved["id"], deleted=deletable["id"], table=table["id"], guidance=guidance["id"])
            page.goto(editor_url)
            expect(block(page, body["id"])).to_be_visible(timeout=30000)
            edit_text(page, body["id"], body["text"] + "（A06 已修改）")
            edit_text(page, renamed["id"], renamed["text"] + "（改名）")
            page.get_by_test_id("tpl-edit-level").select_option("3")
            edit_text(page, guidance["id"], "A06 新的编写指导：说明本章应写什么。")
            cells = table["cells"]
            r, c = len(cells) - 1, len(cells[-1]) - 1
            grid = block(page, table["id"])
            grid.scroll_into_view_if_needed()
            grid.locator(f'td[data-cell="{r}:{c}"] textarea').fill("A06 单元格")
            select(page, moved["id"])
            page.get_by_test_id("tpl-move-down").click()
            select(page, deletable["id"])
            page.get_by_test_id("tpl-delete").click()
            if page.get_by_test_id("tpl-confirm").is_visible():
                page.get_by_test_id("tpl-confirm-ok").click()
            expect(block(page, deletable["id"])).to_have_count(0)
            last = top(base)[-1]
            select(page, last["id"])
            page.get_by_test_id("tpl-insert-heading").click()
            page.get_by_test_id("tpl-edit-text").fill("新增章节（A06）")
            page.get_by_test_id("tpl-edit-level").select_option("1")
            page.get_by_test_id("tpl-insert-body").click()
            page.get_by_test_id("tpl-edit-text").fill("新增章节的正文。")
            saved(page)
            draft = admin.call("GET", f"/template-documents/{td}/draft")["draft"]
            texts = {item["id"]: item for item in draft["structure"]["blocks"]}
            assert texts[body["id"]]["text"].endswith("（A06 已修改）")
            assert texts[renamed["id"]]["headingLevel"] == 3
            assert deletable["id"] not in texts
            assert any(item["text"] == "新增章节（A06）" and item["kind"] == "heading" for item in texts.values())
            ids = [item["id"] for item in top(draft["structure"]["blocks"])]
            assert ids.index(moved["id"]) > ids.index(level1[-1]["id"]), "章节没有移到后面"
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("基于第 1 版")
            shot(page, "A06-03-编辑草稿")
        t2()

        @step("T3 A6-14 改“正文”字号为小四：300ms 防抖后显示影响段数，与接口一致；查看这 M 段高亮")
        def t3():
            page.get_by_test_id("tpl-tab-format").click()
            with page.expect_response(lambda response: "draft:format-impact" in response.url) as info:
                page.get_by_test_id("tpl-format-size-正文").select_option("12")
            item = info.value.json()["data"]["items"][0]
            hint = page.get_by_test_id("tpl-format-impact").locator("[data-affected]")
            expect(hint).to_have_attribute("data-affected", str(item["affectedParagraphs"]))
            expect(hint).to_have_attribute("data-direct", str(item["directFormatParagraphs"]))
            expect(hint).to_contain_text(f"共 {item['affectedParagraphs']} 段")
            if item["directBlockIds"]:
                page.get_by_test_id("tpl-format-impact-view").click()
                expect(page.locator(".blk.hit").first).to_be_visible()
            state["impact"] = item
            saved(page)
            shot(page, "A06-04-样式影响预览")
        t3()

        @step("T4 发布弹窗：摘要含样式影响行、已有文档不受影响；发布第 2 版")
        def t4():
            page.get_by_test_id("template-publish").click()
            dialog = page.get_by_test_id("tpl-publish-dialog")
            expect(dialog.get_by_test_id("tpl-publish-summary")).to_contain_text("标题", timeout=30000)
            expect(dialog.get_by_test_id("tpl-publish-impact")).to_contain_text(f"影响 {state['impact']['affectedParagraphs']} 段")
            expect(dialog.get_by_test_id("tpl-publish-usage")).to_contain_text("已有 1 篇文档")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(dialog.locator(".field-error")).to_contain_text("请填写变更说明")
            dialog.get_by_test_id("tpl-publish-note").fill("A06 验收：L2 + 样式修改")
            shot(page, "A06-05-发布弹窗")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已发布第 2 版", timeout=120000)
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("当前第 2 版")
            page.get_by_test_id("tpl-tab-versions").click()
            expect(page.get_by_test_id("tpl-version-item")).to_have_count(2)
            listing = admin.call("GET", f"/template-documents/{td}/versions")
            state["v1"] = next(item["versionId"] for item in listing["items"] if item["seq"] == 1)
            state["v2"] = listing["current"]["versionId"]
            original = admin.client.get(f"/files/{listing['original']['fileId']}/content")
            assert hashlib.sha256(original.content).hexdigest() == listing["original"]["sha256"]
            assert hashlib.sha256(SRS.read_bytes()).hexdigest() == listing["original"]["sha256"]
            shot(page, "A06-06-版本面板")
        t4()

        @step("T5 A6-3 对比第 1 版与第 2 版：各类改动都在，格式列出“正文 · 字号”")
        def t5():
            page.locator('[data-testid=tpl-version-item][data-seq="2"]').get_by_test_id("tpl-version-compare").click()
            dialog = page.get_by_test_id("tpl-compare-dialog")
            summary = dialog.get_by_test_id("tpl-compare-summary")
            for word in ("标题", "正文", "表格", "指导", "格式"):
                expect(summary).to_contain_text(word, timeout=30000)
            expect(dialog.get_by_test_id("tpl-compare-styles")).to_contain_text("字号")
            shot(page, "A06-07-版本对比")
            dialog.get_by_role("button", name="关闭", exact=True).click()
            diff = admin.call("GET", f"/template-documents/{td}/versions/{state['v1']}/diff/{state['v2']}")
            assert all(diff["summary"][key] >= 1 for key in ("headings", "body", "tables", "guidance", "format")), diff["summary"]
        t5()

        @step("T6 A6-2/A6-7 新文档用第 2 版；老文档仍是第 1 版，工作台提示模板有新版本，不提供升级")
        def t6():
            doc = admin.call("POST", f"/projects/{pid}/documents", json={"name": "需求规格（第 2 版建）", "sourceType": "template", "templateDocumentId": td})
            info = admin.call("GET", f"/documents/{doc['id']}")["template"]
            assert info["seq"] == 2 and not info["legacySnapshot"], info
            assert "新增章节（A06）" in admin.call("GET", f"/documents/{doc['id']}")["markdownContent"]
            old = admin.call("GET", f"/documents/{data['old_doc']['id']}")
            assert old["template"]["seq"] == 1 and old["template"]["latestSeq"] == 2
            assert "新增章节（A06）" not in old["markdownContent"]
            page.goto(f"{WEB}/projects/{pid}/documents/{doc['id']}")
            expect(page.get_by_test_id("doc-template-tag")).to_contain_text("第 2 版", timeout=30000)
            expect(page.get_by_test_id("doc-template-newer")).to_have_count(0)
            page.goto(f"{WEB}/projects/{pid}/documents/{data['old_doc']['id']}")
            expect(page.get_by_test_id("doc-template-tag")).to_contain_text("第 1 版", timeout=30000)
            expect(page.get_by_test_id("doc-template-newer")).to_be_visible()
            shot(page, "A06-08-老文档仍用第1版")
        t6()

        @step("T7 A6-8/9/10 三态表格与目录锁：none 只读并引导上传；cells 仅可改文字；目录按删除键不删")
        def t7():
            page.goto(editor_url)
            expect(page.locator(".paper .blk").first).to_be_visible(timeout=30000)
            expect(page.locator('[data-testid=tpl-table][data-editable="none"]').first).to_contain_text("此表不支持在线编辑")
            none = page.locator('[data-testid=tpl-table][data-editable="none"]').first
            expect(none.locator("textarea")).to_have_count(0)
            expect(page.locator('[data-testid=tpl-table][data-editable="cells"]').first).to_contain_text("仅可改文字")
            partial = page.locator('[data-testid=tpl-table][data-editable="partial"]')
            if partial.count():
                expect(partial.first.locator("td.locked").first).to_have_attribute("title", re.compile("上传为新草稿"))
            toc = page.locator('.blk[data-kind="toc"]')
            if toc.count():
                toc.first.focus()
                page.keyboard.press("Delete")
                expect(page.get_by_test_id("tpl-notice")).to_contain_text("目录不能在线删除")
                expect(toc).to_have_count(toc.count())
            none.get_by_role("button", name="在 Word 中修改后上传为新草稿").click()
            expect(page.get_by_test_id("tpl-upload-dialog")).to_be_visible()
            shot(page, "A06-09-表格三态与上传入口")
            page.get_by_test_id("tpl-upload-dialog").get_by_role("button", name="取消").click()
        t7()

        @step("T8 A6-9 上传新 Word 作为草稿：对齐核对，未落位指导处理完之前不能发布，处理后发布第 3 版")
        def t8():
            from docx import Document
            current = admin.call("GET", f"/template-documents/{td}/versions/{state['v2']}")
            kind = "edited" if current["hasEditedFile"] else "source"
            word = Document(BytesIO(admin.client.get(f"/template-documents/{td}/versions/{state['v2']}/file?kind={kind}").content))
            guidance = [item for item in top(current["structure"]["blocks"]) if item["kind"] == "guidance" and item["path"]]
            target = guidance[-1]["text"].strip()
            body_text = next(item["text"] for item in top(current["structure"]["blocks"]) if item["kind"] == "body" and item["path"] and not item.get("lock", {}).get("text"))
            removed = edited = False
            for paragraph in word.paragraphs:
                if not removed and paragraph.text.strip() == target:
                    paragraph._element.getparent().remove(paragraph._element)
                    removed = True
                elif not edited and paragraph.text.strip() == body_text.strip() and paragraph.runs:
                    paragraph.runs[-1].text += "（Word 中修改）"
                    edited = True
            assert removed and edited, (removed, edited)
            buffer = BytesIO()
            word.save(buffer)
            path = OUT / "a06-upload.docx"
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buffer.getvalue())
            page.get_by_test_id("template-upload").click()
            dialog = page.get_by_test_id("tpl-upload-dialog")
            dialog.locator("input[type=file]").set_input_files(str(path))
            dialog.get_by_test_id("tpl-upload-submit").click()
            expect(page.get_by_test_id("tpl-alignment")).to_contain_text("已对齐", timeout=120000)
            expect(page.get_by_test_id("tpl-unplaced-item")).to_have_count(1)
            expect(page.get_by_test_id("template-publish")).to_be_disabled()
            expect(page.get_by_test_id("template-publish")).to_have_attribute("title", re.compile("还有 1 条编写指导没有指定位置"))
            shot(page, "A06-10-上传对齐核对")
            page.get_by_test_id("tpl-unplaced-place").click()
            page.get_by_test_id("tpl-unplaced-heading").select_option(index=2)
            expect(page.get_by_test_id("tpl-unplaced-item")).to_have_count(0)
            saved(page)
            expect(page.get_by_test_id("template-publish")).to_be_enabled()
            page.get_by_test_id("template-publish").click()
            dialog = page.get_by_test_id("tpl-publish-dialog")
            dialog.get_by_test_id("tpl-publish-note").fill("A06 验收：上传修改后的 Word")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已发布第 3 版", timeout=120000)
        t8()

        @step("T9 A6-6 并发：别人先保存了草稿，后保存的一方看到 409 提示条和下载按钮")
        def t9():
            page.goto(editor_url)
            expect(page.locator(".paper .blk").first).to_be_visible(timeout=30000)
            maint = Api(f"maint{RUN}")
            draft = maint.call("GET", f"/template-documents/{td}/draft")["base"]
            structure = draft["structure"]
            heads = [item for item in top(structure["blocks"]) if item["kind"] == "heading"]
            heads[0]["text"] += "（维护甲）"
            maint.call("PUT", f"/template-documents/{td}/draft", json={"structure": structure, "format": draft["format"], "expectedDraftRevision": 0})
            edit_text(page, heads[1]["id"], heads[1]["text"] + "（管理员）")
            expect(page.get_by_test_id("tpl-conflict")).to_contain_text("修改了草稿", timeout=30000)
            expect(page.get_by_test_id("tpl-conflict-download")).to_be_visible()
            with page.expect_download() as info:
                page.get_by_test_id("tpl-conflict-download").click()
            assert info.value.suggested_filename.endswith("-我的修改.md"), info.value.suggested_filename
            with page.expect_download() as info:
                page.get_by_test_id("tpl-conflict-download-json").click()
            assert info.value.suggested_filename.endswith("-我的草稿.json"), info.value.suggested_filename
            shot(page, "A06-11-并发409")
            current = maint.call("GET", f"/template-documents/{td}/draft")["draft"]
            maint.call("DELETE", f"/template-documents/{td}/draft?expectedDraftRevision={current['draftRevision']}")
        t9()

        @step("T10 A6-13 草稿过期：合并到最新版本，1 处冲突未选时合并禁用，保留我的后合并，再发布")
        def t10():
            page.goto(editor_url)
            expect(page.locator(".paper .blk").first).to_be_visible(timeout=30000)
            maint = Api(f"maint{RUN}")
            base_payload = maint.call("GET", f"/template-documents/{td}/draft")["base"]
            structure = base_payload["structure"]
            heads = [item for item in top(structure["blocks"]) if item["kind"] == "heading" and not item["lock"]["text"]]
            a, b, c = heads[2], heads[3], heads[4]
            state["rebase"] = (a["id"], b["id"], c["id"], a["text"], b["text"], c["text"])
            for item in (a, b):
                item["text"] += "（甲改）"
            saved_draft = maint.call("PUT", f"/template-documents/{td}/draft", json={"structure": structure, "format": base_payload["format"], "expectedDraftRevision": 0})
            maint.call("POST", f"/template-documents/{td}/versions", json={"draftRevision": saved_draft["draftRevision"], "note": "维护甲先发布"})
            # 管理员仍在第 3 版上编辑：改 b、c
            edit_text(page, b["id"], state["rebase"][4] + "（我改）")
            edit_text(page, c["id"], state["rebase"][5] + "（我改）")
            saved(page)
            page.get_by_test_id("template-publish").click()
            dialog = page.get_by_test_id("tpl-publish-dialog")
            dialog.get_by_test_id("tpl-publish-note").fill("管理员后发布")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(page.get_by_test_id("tpl-stale")).to_contain_text("已发布第 4 版", timeout=60000)
            shot(page, "A06-12-草稿过期提示")
            page.get_by_test_id("tpl-stale-rebase").click()
            rebase = page.get_by_test_id("tpl-rebase-dialog")
            expect(rebase.get_by_test_id("tpl-rebase-conflict")).to_have_count(1, timeout=30000)
            expect(rebase).to_contain_text("2 处变化")
            expect(rebase.get_by_test_id("tpl-rebase-merge")).to_be_disabled()
            expect(rebase.get_by_test_id("tpl-rebase-counter")).to_have_text("已选择 0/1 处")
            shot(page, "A06-13-合并冲突")
            rebase.get_by_test_id("tpl-rebase-take-mine").check()
            expect(rebase.get_by_test_id("tpl-rebase-counter")).to_have_text("已选择 1/1 处")
            rebase.get_by_test_id("tpl-rebase-merge").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已合并到第 4 版", timeout=30000)
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("基于第 4 版")
            merged = {item["id"]: item["text"] for item in admin.call("GET", f"/template-documents/{td}/draft")["draft"]["structure"]["blocks"]}
            assert merged[a["id"]].endswith("（甲改）") and merged[b["id"]].endswith("（我改）") and merged[c["id"]].endswith("（我改）"), merged
            page.get_by_test_id("template-publish").click()
            dialog = page.get_by_test_id("tpl-publish-dialog")
            dialog.get_by_test_id("tpl-publish-note").fill("合并后发布")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已发布第 5 版", timeout=120000)
        t10()

        @step("T11 A6-4 把第 1 版恢复为草稿并发布为第 6 版，变更说明预填“恢复自第 1 版”；第 2 版仍可查看")
        def t11():
            page.get_by_test_id("tpl-tab-versions").click()
            page.locator('[data-testid=tpl-version-item][data-seq="1"]').get_by_test_id("tpl-version-restore").click()
            page.get_by_test_id("tpl-confirm-ok").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已把第 1 版恢复为草稿", timeout=60000)
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("恢复自第 1 版")
            page.get_by_test_id("template-publish").click()
            dialog = page.get_by_test_id("tpl-publish-dialog")
            expect(dialog.get_by_test_id("tpl-publish-note")).to_have_value("恢复自第 1 版")
            dialog.get_by_test_id("tpl-publish-submit").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已发布第 6 版", timeout=120000)
            listing = admin.call("GET", f"/template-documents/{td}/versions")
            latest = next(item for item in listing["items"] if item["seq"] == 6)
            assert latest["restoredFrom"]["seq"] == 1 and latest["note"] == "恢复自第 1 版"
            assert admin.raw("GET", f"/template-documents/{td}/versions/{state['v2']}/file?kind=source").status_code == 200
            page.get_by_test_id("tpl-tab-versions").click()
            expect(page.get_by_test_id("tpl-version-item")).to_have_count(6)
            shot(page, "A06-14-恢复后第6版")
        t11()

        @step("T12 A6-5 项目创建者经地址打开编辑器为只读：没有发布和上传按钮")
        def t12():
            reader = browser.new_context(viewport=VIEWPORT).new_page()
            open_pages.append(reader)
            login(reader, f"creator{RUN}")
            reader.goto(editor_url)
            expect(reader.locator(".paper .blk").first).to_be_visible(timeout=30000)
            expect(reader.get_by_test_id("template-publish")).to_have_count(0)
            expect(reader.get_by_test_id("template-upload")).to_have_count(0)
            expect(reader.get_by_test_id("save-status")).to_contain_text("只读")
            expect(reader.get_by_test_id("tpl-insert-body")).to_be_disabled()
            shot(reader, "A06-15-项目创建者只读")
            reader.context.close()
        t12()

        @step("T13 A6-12 审计员：无模板导航、模板地址 403 页、GET versions 403，文档里能看到模板版本")
        def t13():
            auditor = Api(f"auditor{RUN}")
            assert auditor.raw("GET", f"/template-documents/{td}/versions").status_code == 403
            viewer = browser.new_context(viewport=VIEWPORT).new_page()
            open_pages.append(viewer)
            login(viewer, f"auditor{RUN}")
            expect(viewer.locator('aside[aria-label="主导航"] >> text=模板管理')).to_have_count(0)
            viewer.goto(editor_url)
            expect(viewer.get_by_text("你没有编辑模板权限")).to_be_visible(timeout=30000)
            shot(viewer, "A06-16-审计员403")
            viewer.goto(f"{WEB}/projects/{pid}/documents/{data['old_doc']['id']}")
            expect(viewer.get_by_test_id("doc-template-tag")).to_contain_text("第 1 版", timeout=30000)
            viewer.context.close()
        t13()

        @step("T14 修复轮：弹窗初始焦点与焦点归还（25 M-4）、常驻放弃草稿（M-3）、别人发布了我打开的草稿后继续编辑走过期合并（D-8）")
        def t14():
            focused = lambda: page.evaluate("() => { const e = document.activeElement; return e ? (e.id || e.dataset.testid || (e.hasAttribute('data-confirm-cancel') ? 'confirm-cancel' : e.tagName)) : '' }")
            page.goto(editor_url)
            expect(page.locator(".paper .blk").first).to_be_visible(timeout=30000)
            page.get_by_test_id("template-upload").focus()
            page.get_by_test_id("template-upload").click()
            expect(page.get_by_test_id("tpl-upload-dialog")).to_be_visible()
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'tpl-upload-file'", timeout=5000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => document.activeElement && document.activeElement.dataset.testid === 'template-upload'", timeout=5000)
            heads = [item for item in top(admin.call("GET", f"/template-documents/{td}/draft")["base"]["structure"]["blocks"])
                     if item["kind"] == "heading" and not item["lock"]["text"]]
            edit_text(page, heads[1]["id"], heads[1]["text"] + "（T14）")
            saved(page)
            page.get_by_test_id("template-publish").click()
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'tpl-publish-note'", timeout=5000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => document.activeElement && document.activeElement.dataset.testid === 'template-publish'", timeout=5000)
            page.get_by_test_id("tpl-discard-draft").click()
            expect(page.get_by_test_id("tpl-confirm")).to_be_visible()
            assert focused() == "confirm-cancel", focused()
            page.get_by_test_id("tpl-confirm-ok").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已放弃草稿", timeout=30000)
            expect(page.get_by_test_id("tpl-draft-label")).to_contain_text("当前第 6 版")
            assert admin.call("GET", f"/template-documents/{td}/draft")["draft"] is None
            # D-8：我打开草稿后，维护甲把这份草稿发布了；我继续改 → 服务端按第 6 版重建草稿 → 过期提示（写明谁发布了第 7 版）
            edit_text(page, heads[2]["id"], heads[2]["text"] + "（我先改）")
            saved(page)
            maint = Api(f"maint{RUN}")
            current = maint.call("GET", f"/template-documents/{td}/draft")["draft"]
            maint.call("POST", f"/template-documents/{td}/versions", json={"draftRevision": current["draftRevision"], "note": "维护甲发布了别人的草稿"})
            edit_text(page, heads[3]["id"], heads[3]["text"] + "（我后改）")
            expect(page.get_by_test_id("tpl-stale")).to_contain_text("已发布第 7 版", timeout=30000)
            shot(page, "A06-17-草稿被发布后继续编辑")
            rebuilt = admin.call("GET", f"/template-documents/{td}/draft")["draft"]
            assert rebuilt["parentVersion"]["seq"] == 6, rebuilt["parentVersion"]
            assert any(item["text"].endswith("（我后改）") for item in rebuilt["structure"]["blocks"])
            page.get_by_test_id("tpl-stale-discard").click()
            page.get_by_test_id("tpl-confirm-ok").click()
            expect(page.get_by_test_id("tpl-notice")).to_contain_text("已放弃草稿", timeout=30000)
        t14()

        browser.close()
    failed = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    for name, _, message in failed:
        print(f"FAILED {name}: {message}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
