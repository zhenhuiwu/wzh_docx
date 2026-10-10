"""阶段2 浏览器验收（S8）：1920×1080，非管理员账号，服务只用 8041/5211，数据库在 /tmp/wzh_phase2_run/live。

运行（先用 /tmp/wzh_phase2_run/live/start.sh 起服务）：
    DATABASE_URL=sqlite:////tmp/wzh_phase2_run/live/app.db FILE_DATA_DIR=/tmp/wzh_phase2_run/live/files \
        uv run python tests/e2e_phase2.py
覆盖：成员可见项目与文档列表、438C 模板文档编辑保存与导出、两账号不同段落合并、同段冲突保留草稿并在面板处理、离线恢复。
截图写入 /workspace/wzh_collab/screens/phase2/。
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8041/api/v1"
WEB = "http://127.0.0.1:5211"
SHOTS = Path(os.environ.get("E2E_SHOTS", "/workspace/wzh_collab/screens/phase2"))
PASSWORD = "E2e-phase2-pass-1"
RUN = uuid.uuid4().hex[:6]
results: list[tuple[str, bool, str]] = []
open_pages: list = []


def step(name: str):
    def wrap(fn):
        def run(*args, **kwargs):
            try:
                fn(*args, **kwargs)
                results.append((name, True, ""))
                print(f"PASS {name}", flush=True)
            except Exception as error:  # noqa: BLE001 - 汇总后统一失败
                results.append((name, False, f"{type(error).__name__}: {error}"))
                print(f"FAIL {name}: {error}", flush=True)
                for index, page in enumerate(open_pages):
                    if not page.is_closed():
                        page.screenshot(path=f"/tmp/wzh_phase2_run/live/fail-{index}.png")
                raise
        return run
    return wrap


class Api:
    def __init__(self, account: str):
        self.client = httpx.Client(base_url=API, timeout=120)
        csrf = self.client.get("/auth/csrf").json()["data"]["csrfToken"]
        response = self.client.post("/auth/login", json={"account": account, "password": PASSWORD}, headers={"X-CSRF-Token": csrf})
        response.raise_for_status()
        self.csrf = response.json()["data"]["csrfToken"]

    def call(self, method: str, path: str, **kwargs):
        headers = {"X-CSRF-Token": self.csrf, **kwargs.pop("headers", {})}
        response = self.client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise AssertionError(f"{method} {path} → {response.status_code} {response.text[:300]}")
        return response.json()["data"] if response.content else None


def seed() -> dict:
    sys.path.insert(0, str(ROOT / "server"))
    from app.db import connect
    from app.identity import create_user
    users = {}
    with connect() as connection:
        users["admin"] = create_user(account=f"e2e-admin-{RUN}", name="验收管理员", password=PASSWORD, roles=["system_admin"], connection=connection)
        for key, name in (("zhang", "张工"), ("li", "李工"), ("wang", "王工")):
            # 与 visual 的新建用户一致：globalRoles 默认 []，只靠项目角色。
            users[key] = create_user(account=f"e2e-{key}-{RUN}", name=name, password=PASSWORD, roles=[], allow_no_roles=True, connection=connection)
        connection.commit()
    admin = Api(f"e2e-admin-{RUN}")
    project = admin.call("POST", "/projects", json={"name": f"阶段2验收项目-{RUN}", "ownerId": str(users["zhang"]), "status": "active"},
                         headers={"Idempotency-Key": f"e2e-project-{RUN}"})
    for key, role in (("li", "editor"), ("wang", "viewer")):
        revision = admin.call("GET", f"/projects/{project['id']}")["revision"]
        admin.call("PUT", f"/projects/{project['id']}/members/{users[key]}",
                   json={"userId": str(users[key]), "role": role, "expectedProjectRevision": revision})
    zhang = Api(f"e2e-zhang-{RUN}")
    source = next((ROOT / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
    upload = zhang.client.post("/files", files={"file": (source.name, source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"purpose": "attachment", "projectId": project["id"]},
                               headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"e2e-upload-{RUN}"})
    assert upload.status_code == 201, upload.text
    document = zhang.call("POST", f"/projects/{project['id']}/documents",
                          json={"name": "软件需求规格说明", "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]})
    # 本机（Linux）没有 Microsoft Word：带目录域的 438C 模板导出需要 Word 刷新目录，会返回 503 WORD_UNAVAILABLE。
    # 另建一份不带目录的 Word 走界面导出下载；438C 文档的导出管线（edit_original_docx）在脚本内直接校验。
    from docx import Document as WordDocument
    from io import BytesIO
    plain = WordDocument()
    plain.add_heading("概述", 1)
    plain.add_paragraph("本文档用于验证界面导出下载的完整链路。")
    buffer = BytesIO()
    plain.save(buffer)
    upload2 = zhang.client.post("/files", files={"file": ("导出验收.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                                data={"purpose": "attachment", "projectId": project["id"]},
                                headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"e2e-upload2-{RUN}"})
    assert upload2.status_code == 201, upload2.text
    plain_doc = zhang.call("POST", f"/projects/{project['id']}/documents",
                           json={"name": "导出验收", "sourceType": "upload", "wordFileId": upload2.json()["data"]["id"]})
    return {"users": users, "project": project, "document": document, "plain": plain_doc, "zhang": zhang, "source": source}


def login(page: Page, account: str):
    page.goto(f"{WEB}/login")
    page.fill("#staff-id", account)
    page.fill("#password", PASSWORD)
    page.locator("form button[type=submit]").click()
    page.wait_for_url(re.compile(r"/projects"), timeout=30000)


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def open_doc(page: Page, data: dict):
    page.goto(f"{WEB}/projects/{data['project']['id']}/documents/{data['document']['id']}")
    expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
    expect(page.get_by_test_id("save-status")).to_contain_text(re.compile("保存"), timeout=30000)


def paragraph(page: Page, text: str):
    return page.locator(".paper-content p", has_text=text).first


def type_after(page: Page, text: str, addition: str):
    target = paragraph(page, text)
    target.scroll_into_view_if_needed()
    target.click()
    page.keyboard.press("End")
    page.keyboard.type(addition)


def save_now(page: Page):
    page.keyboard.press("Control+s")


def wait_saved(page: Page, timeout: int = 30000):
    expect(page.get_by_test_id("save-status")).to_contain_text(re.compile("刚刚保存|已保存"), timeout=timeout)


def pick_paragraphs(markdown: str, count: int) -> list[str]:
    picked = []
    for block in markdown.split("\n\n"):
        block = block.strip()
        if not block or block[0] in "#|!-*>0123456789[" or "\n" in block or len(block) < 12 or any(c in block for c in "*_`[]<>\\"):
            continue
        key = block[:10]
        if any(key in item or item[:10] in block for item in picked):
            continue
        picked.append(block[:10])
        if len(picked) == count:
            break
    assert len(picked) == count, picked
    return picked


def main() -> int:
    data = seed()
    markdown = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
    p_export, p_a, p_b, p_conflict, p_offline = pick_paragraphs(markdown, 5)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ctx_a = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True, locale="zh-CN")
        ctx_b = browser.new_context(viewport={"width": 1920, "height": 1080}, accept_downloads=True, locale="zh-CN")
        a, b = ctx_a.new_page(), ctx_b.new_page()
        open_pages.extend([a, b])
        login(a, f"e2e-zhang-{RUN}")
        login(b, f"e2e-li-{RUN}")

        @step("成员可见项目与文档列表（P0-4 / N-S5）")
        def members_visible():
            expect(b.get_by_text(data["project"]["name"]).first).to_be_visible(timeout=30000)
            b.goto(f"{WEB}/projects/{data['project']['id']}/documents")
            expect(b.get_by_text("软件需求规格说明").first).to_be_visible(timeout=30000)
            expect(b.get_by_text("项目不存在或你无权访问")).to_have_count(0)
            shot(b, "01-成员-文档列表")
            viewer = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN").new_page()
            login(viewer, f"e2e-wang-{RUN}")
            viewer.goto(f"{WEB}/projects/{data['project']['id']}/documents")
            expect(viewer.get_by_text("软件需求规格说明").first).to_be_visible(timeout=30000)
            expect(viewer.get_by_role("button", name="新建文档")).to_have_count(0)
            shot(viewer, "02-只读成员-文档列表")
            viewer.context.close()
        members_visible()

        @step("438C 模板文档编辑保存并导出")
        def edit_and_export():
            open_doc(a, data)
            type_after(a, p_export, "（张工编辑保存）")
            save_now(a)
            wait_saved(a)
            shot(a, "03-编辑保存")
            server = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
            assert "（张工编辑保存）" in server
            # 438C 文档：导出管线写回原 Word（目录刷新需 Windows Word，本机跳过）
            from tempfile import TemporaryDirectory
            from docx import Document as WordDocument
            from app.docx_export import edit_original_docx
            with TemporaryDirectory() as directory:
                output = Path(directory) / "out.docx"
                edit_original_docx(server, data["source"], output)
                assert any("（张工编辑保存）" in p.text for p in WordDocument(str(output)).paragraphs)
            a.get_by_test_id("export-word").click()
            expect(a.get_by_text(re.compile("导出失败：.*Microsoft Word"))).to_be_visible(timeout=60000)
            # 不带目录的文档：界面导出下载
            a.goto(f"{WEB}/projects/{data['project']['id']}/documents/{data['plain']['id']}")
            expect(a.locator(".paper-content")).to_be_visible(timeout=60000)
            type_after(a, "完整链路", "（导出前编辑）")
            save_now(a)
            wait_saved(a)
            with a.expect_download(timeout=120000) as download:
                a.get_by_test_id("export-word").click()
            path = download.value.path()
            assert any("（导出前编辑）" in p.text for p in WordDocument(str(path)).paragraphs)
            shot(a, "03b-导出下载")
        edit_and_export()

        @step("两账号改不同段落自动合并")
        def merge_two_accounts():
            open_doc(a, data)
            open_doc(b, data)  # B 打开时的版本
            type_after(a, p_a, "（张工改A段）")
            save_now(a)
            wait_saved(a)
            type_after(b, p_b, "（李工改B段）")
            save_now(b)
            wait_saved(b)
            expect(b.get_by_text(re.compile("张工更新了 \\d+ 处内容，已合并到你的文档"))).to_be_visible(timeout=15000)
            expect(paragraph(b, "（张工改A段）")).to_be_visible(timeout=15000)
            shot(b, "04-两人合并-提示")
            server = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
            assert "（张工改A段）" in server and "（李工改B段）" in server
        merge_two_accounts()

        @step("同段冲突保留草稿，面板可处理")
        def same_paragraph_conflict():
            open_doc(a, data)
            open_doc(b, data)
            type_after(a, p_conflict, "（张工的写法）")
            save_now(a)
            wait_saved(a)
            type_after(b, p_conflict, "（李工的写法）")
            save_now(b)
            panel = b.locator(".conflict-panel")
            expect(panel).to_be_visible(timeout=20000)
            expect(panel.locator(".ccard")).to_have_count(1, timeout=20000)
            expect(panel.locator(".ccard-side.mine")).to_contain_text("（李工的写法）")
            expect(panel.locator(".ccard-side.theirs")).to_contain_text("（张工的写法）")
            expect(b.get_by_test_id("conflict-toggle")).to_contain_text("冲突 1")
            # 冲突部分不写入正文：编辑区显示对方（已在正文中）的版本，我的版本只在面板里
            expect(paragraph(b, "（张工的写法）")).to_be_visible(timeout=15000)
            expect(b.locator(".paper-content", has_text="（李工的写法）")).to_have_count(0, timeout=15000)
            wait_saved(b)
            shot(b, "05-同段冲突-面板")
            panel.get_by_role("button", name=re.compile("保留我的")).click()
            expect(panel.locator(".ccard")).to_have_count(0, timeout=20000)
            expect(b.get_by_test_id("conflict-toggle")).to_have_count(0, timeout=20000)
            server = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
            assert "（李工的写法）" in server, "保留我的版本后正文应为李工的写法"
            expect(paragraph(b, "（李工的写法）")).to_be_visible(timeout=15000)
            shot(b, "06-同段冲突-处理后")
        same_paragraph_conflict()

        @step("离线编辑本机保留，联网后自动保存；关闭后重开可恢复")
        def offline_recovery():
            open_doc(b, data)
            ctx_b.set_offline(True)
            type_after(b, p_offline, "（离线写的一句）")
            save_now(b)
            expect(b.get_by_test_id("save-status")).to_contain_text(re.compile("离线|重试|未保存"), timeout=30000)
            shot(b, "07-离线-本机保留")
            ctx_b.set_offline(False)
            b.evaluate("window.dispatchEvent(new Event('online'))")
            wait_saved(b, 60000)
            server = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
            assert "（离线写的一句）" in server
            # 关闭页面前断网：草稿留在本机，重新打开后“恢复并保存”
            ctx_b.set_offline(True)
            type_after(b, p_offline, "（断网关页）")
            b.wait_for_timeout(500)
            b.close()
            ctx_b.set_offline(False)
            page = ctx_b.new_page()
            open_doc(page, data)
            bar = page.get_by_test_id("draft-recovery")
            expect(bar).to_be_visible(timeout=20000)
            shot(page, "08-重开-草稿恢复条")
            bar.get_by_role("button", name="恢复并保存").click()
            expect(bar).to_have_count(0, timeout=30000)
            wait_saved(page)
            server = data["zhang"].call("GET", f"/documents/{data['document']['id']}")["markdownContent"]
            assert "（断网关页）" in server
            shot(page, "09-草稿恢复后")
        offline_recovery()

        browser.close()
    failed = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
