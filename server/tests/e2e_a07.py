"""A07 编写分工（硬限制）浏览器验收（16 §A07、17 §4.5，1920×1080 与 1366×768）。

环境变量：E2E_API、E2E_WEB、DATABASE_URL、FILE_DATA_DIR 必须设置（脚本直接在被测库里建账号）；
E2E_VIEWPORT（如 1366x768，默认 1920x1080）、E2E_SHOTS、E2E_OUT。admin/admin123 登录、不改密。
覆盖：G1 负责人在工作台“编写分工”面板按章节分配、模式标签；G2 编写人员只能改分给自己的章节（其余只读，点了有提示）；
G3 保存过程中被改分工 → 409 SECTION_NOT_ASSIGNED 提示条、不自动重试、可下载；G4 接口：越权 409、缺 blockIds 422、负责人不受限；
G5 编写人员新建的文档整篇归创建人；G6 编写人员不能恢复版本；G7 成员表“编写分工”列、点数字到概述按人视图；
G8 S-5 降为只读前确认（取消不改、确认后分工清除）；G9 概述“编写分工”按文档/按人。
"""

from __future__ import annotations

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
OUT = Path(os.environ.get("E2E_OUT", str(Path(tempfile.gettempdir()) / "e2e_a07")))
SHOTS = Path(os.environ.get("E2E_SHOTS", str(OUT / "shots")))
PASSWORD = "E2e-a07-pass-1"
ADMIN, ADMIN_PASSWORD = "admin", "admin123"
RUN = uuid.uuid4().hex[:6]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
        self.client = httpx.Client(base_url=API, timeout=120)
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

    def members_revision(self, project: str) -> int:
        return self.call("GET", f"/projects/{project}/members")["membersRevision"]

    def set_role(self, project: str, user, role: str):
        """已是成员 → PUT 改角色；不是成员 → members:batch 加入（PUT 只改已有成员，A05 20 I-3）。"""
        snapshot = self.call("GET", f"/projects/{project}/members")
        if any(item["userId"] == str(user) for item in snapshot["items"]):
            return self.call("PUT", f"/projects/{project}/members/{user}", json={"role": role, "expectedMembersRevision": snapshot["membersRevision"]})
        return self.call("POST", f"/projects/{project}/members:batch",
                         json={"userIds": [str(user)], "role": role, "expectedMembersRevision": snapshot["membersRevision"]})

    def project(self, name: str, owner) -> dict:
        return self.call("POST", "/projects", json={"name": name, "ownerId": str(owner), "status": "active"},
                         headers={"Idempotency-Key": f"a07-{uuid.uuid4().hex}"})





VIEW = os.environ.get("E2E_VIEWPORT", "1920x1080")
WIDTH, HEIGHT = (int(value) for value in VIEW.split("x"))
TAG = "" if VIEW == "1920x1080" else f"-{VIEW}"


def make_word(title_paragraphs: list[tuple[str, list[str]]]) -> bytes:
    from docx import Document as WordDocument
    word = WordDocument()
    for title, paragraphs in title_paragraphs:
        word.add_heading(title, 1)
        for text in paragraphs:
            word.add_paragraph(text)
    buffer = BytesIO()
    word.save(buffer)
    return buffer.getvalue()


def upload_doc(api: "Api", project_id: str, name: str, content: bytes) -> dict:
    upload = api.client.post("/files", files={"file": (f"{name}.docx", content, DOCX)},
                             data={"purpose": "attachment", "projectId": project_id},
                             headers={"X-CSRF-Token": api.csrf, "Idempotency-Key": f"a07-upload-{uuid.uuid4().hex}"})
    assert upload.status_code == 201, upload.text
    return api.call("POST", f"/projects/{project_id}/documents",
                    json={"name": name, "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]})


CHAPTERS = [("概述", ["概述第一段：系统的建设背景。", "概述第二段：系统的建设目标。"]),
            ("功能需求", ["功能需求第一段：用户管理。", "功能需求第二段：权限管理。"]),
            ("附录", ["附录第一段：术语表。"])]


def seed() -> dict:
    sys.path.insert(0, str(ROOT / "server"))
    from app.db import connect
    from app.identity import create_user
    users = {}
    with connect() as connection:
        row = connection.execute("select id from users where account_key = %s", (ADMIN,)).fetchone()
        users["admin"] = row[0] if row else create_user(account=ADMIN, name="系统管理员", password=ADMIN_PASSWORD, roles=["system_admin"], connection=connection)
        for key, name in (("lisi", "李四"), ("wangce", "王测"), ("liuyang", "刘洋")):
            users[key] = create_user(account=f"{key}{RUN}", name=name, password=PASSWORD, roles=[], department="软件一部",
                                     allow_no_roles=True, connection=connection)
        connection.commit()
    admin = Api(ADMIN, ADMIN_PASSWORD)
    project = admin.project(f"分工验收项目{RUN}", users["admin"])
    for key in ("lisi", "wangce", "liuyang"):
        admin.set_role(project["id"], users[key], "editor")
    document = upload_doc(admin, project["id"], "软件需求规格说明", make_word(CHAPTERS))
    return {"users": users, "project": project, "document": document, "admin": admin}


def login(page: Page, account: str, password: str = PASSWORD):
    page.context.clear_cookies()
    page.goto(f"{WEB}/login")
    page.fill("#staff-id", account)
    page.fill("#password", password)
    page.locator("form button[type=submit]").click()
    page.wait_for_url(re.compile(r"/projects"), timeout=30000)


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}{TAG}.png"))


def open_doc(page: Page, pid: str, did: str, query: str = ""):
    page.goto(f"{WEB}/projects/{pid}/documents/{did}{query}")
    expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
    expect(page.get_by_test_id("save-status")).to_contain_text(re.compile("保存"), timeout=30000)


def para(page: Page, text: str):
    return page.locator(".paper-content p", has_text=text).first


def wait_saved(page: Page, timeout: int = 30000):
    expect(page.get_by_test_id("save-status")).to_contain_text(re.compile("刚刚保存|已保存"), timeout=timeout)


def heading_ids(api: "Api", did: str) -> dict[str, str]:
    objects = api.call("GET", f"/documents/{did}/objects")["objects"]
    return {item["markdown"].lstrip("# ").strip(): item["id"] for item in objects if item["kind"] == "heading"}


def require_targets() -> None:
    missing = [name for name, value in (("E2E_API", API), ("E2E_WEB", WEB), ("DATABASE_URL", os.environ.get("DATABASE_URL", "")),
                                        ("FILE_DATA_DIR", os.environ.get("FILE_DATA_DIR", ""))) if not value]
    if missing:
        raise SystemExit(f"请设置环境变量 {', '.join(missing)}（被测服务的接口、前端地址和同一个数据库），脚本不使用默认值。")


def main() -> int:  # noqa: C901 - 验收脚本按用例顺序展开
    require_targets()
    data = seed()
    users, admin = data["users"], data["admin"]
    pid, did = data["project"]["id"], data["document"]["id"]
    lisi, wangce = Api(f"lisi{RUN}"), Api(f"wangce{RUN}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ctx_a = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=True)
        ctx_b = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, accept_downloads=True)
        page, page2 = ctx_a.new_page(), ctx_b.new_page()
        open_pages.extend([page, page2])
        login(page, ADMIN, ADMIN_PASSWORD)
        login(page2, f"lisi{RUN}")

        @step("G1 负责人在工作台“编写分工”面板按章节分配，文档显示“按分工编辑”")
        def g1():
            open_doc(page, pid, did)
            expect(page.get_by_test_id("doc-assign-mode-chip")).to_have_text("按分工编辑")
            page.get_by_test_id("assign-open").click()
            panel = page.get_by_test_id("assign-panel")
            expect(panel).to_be_visible()
            expect(panel.get_by_test_id("assign-summary")).to_contain_text("未分配章节 3 个")
            rows = panel.get_by_test_id("assign-heading")
            expect(rows).to_have_count(3)
            rows.filter(has_text="概述").get_by_test_id("assign-select").select_option(str(users["lisi"]))
            expect(panel.get_by_test_id("assign-summary")).to_contain_text("未分配章节 2 个", timeout=15000)
            rows.filter(has_text="功能需求").get_by_test_id("assign-select").select_option(str(users["wangce"]))
            expect(panel.get_by_test_id("assign-summary")).to_contain_text("未分配章节 1 个", timeout=15000)
            expect(rows.filter(has_text="概述").get_by_test_id("assign-select")).to_have_value(str(users["lisi"]))
            shot(page, "A07-01-分工面板")
            view = admin.call("GET", f"/documents/{did}/assignments")
            assert {item["assignees"][0]["name"] for item in view["explicit"]} == {"李四", "王测"}, view["explicit"]
        g1()

        @step("G2 编写人员只能改分给自己的章节：其余只读，点了有提示；自己的章节保存成功")
        def g2():
            open_doc(page2, pid, did)
            expect(page2.get_by_test_id("doc-assign-mode-chip")).to_be_visible()
            mine, theirs, free = para(page2, "概述第一段"), para(page2, "功能需求第一段"), para(page2, "附录第一段")
            expect(theirs).to_have_attribute("data-editable", "false", timeout=15000)
            expect(theirs).to_have_attribute("data-assignee", "王测")
            expect(free).to_have_attribute("data-editable", "false")
            expect(mine).not_to_have_attribute("data-editable", "false")
            expect(page2.locator(".paper-content")).to_have_attribute("contenteditable", "true", timeout=30000)
            mine.click()
            page2.keyboard.press("End")
            page2.keyboard.type("（李四补充）")
            expect(mine).to_contain_text("（李四补充）")
            page2.keyboard.press("Control+s")
            saved = ""
            for _ in range(60):
                saved = admin.call("GET", f"/documents/{did}")["markdownContent"]
                if "（李四补充）" in saved:
                    break
                page2.wait_for_timeout(500)
            wait_saved(page2)
            assert "概述第一段：系统的建设背景。（李四补充）" in saved, saved[:400]
            # 点只读段：不能输入，出现归属提示
            before = admin.call("GET", f"/documents/{did}")["markdownContent"]
            theirs.click()
            page2.keyboard.type("越权")
            page2.keyboard.press("Control+s")
            expect(theirs).not_to_contain_text("越权")
            free.click()
            page2.keyboard.press("Backspace")
            shot(page2, "A07-02-编写人员只读章节")
            page2.wait_for_timeout(1500)
            after = admin.call("GET", f"/documents/{did}")["markdownContent"]
            assert after == before, (before[:400], after[:400])
        g2()

        @step("G2b 在自己段首按退格不会并入别人的段（前端拦下并提示）")
        def g2b():
            first_theirs = para(page2, "功能需求第一段")
            heading = page2.locator(".paper-content h1", has_text="功能需求").first
            expect(heading).to_have_attribute("data-editable", "false")
            para(page2, "概述第二段").click()
            page2.keyboard.press("End")
            page2.keyboard.press("Delete")
            expect(page2.get_by_test_id("locked-hint")).to_contain_text("王测", timeout=5000)
            expect(first_theirs).to_be_visible()
        g2b()

        @step("G3 正在编辑时被改了分工：保存被拒（409），提示条不自动重试、可下载，服务器正文不变")
        def g3():
            heads = heading_ids(admin, did)
            view = admin.call("GET", f"/documents/{did}/assignments")
            mine = para(page2, "概述第二段")
            mine.click()
            page2.keyboard.press("End")
            # 管理员先把“概述”改给王测（李四的页面 45 秒内才会知道），李四紧接着保存
            admin.call("PUT", f"/documents/{did}/assignments",
                       json={"objectIds": [heads["概述"]], "assigneeIds": [str(users["wangce"])], "expectedAssignmentsRevision": view["assignmentsRevision"]})
            before = admin.call("GET", f"/documents/{did}")["markdownContent"]
            page2.keyboard.type("（被拒）")
            page2.keyboard.press("Control+s")
            banner = page2.get_by_test_id("save-blocked")
            expect(banner).to_have_attribute("data-code", "SECTION_NOT_ASSIGNED", timeout=30000)
            expect(banner).to_contain_text("不归你编写")
            expect(banner.get_by_role("button", name="下载我的修改")).to_be_visible()
            expect(banner.get_by_role("button", name="重试保存")).to_have_count(0)
            shot(page2, "A07-03-保存被拒")
            assert admin.call("GET", f"/documents/{did}")["markdownContent"] == before
            # 归属已刷新：概述也锁上了
            expect(para(page2, "概述第一段")).to_have_attribute("data-editable", "false", timeout=15000)
            page2.once("dialog", lambda dialog: dialog.accept())
            banner.get_by_test_id("save-rejected-reload").click()
            expect(page2.get_by_test_id("save-blocked")).to_have_count(0, timeout=30000)
            expect(para(page2, "概述第二段")).not_to_contain_text("（被拒）")
            # 还给李四
            view = admin.call("GET", f"/documents/{did}/assignments")
            admin.call("PUT", f"/documents/{did}/assignments",
                       json={"objectIds": [heads["概述"]], "assigneeIds": [str(users["lisi"])], "expectedAssignmentsRevision": view["assignmentsRevision"]})
        g3()

        @step("G4 接口：越权 409 SECTION_NOT_ASSIGNED、缺 blockIds 422、负责人不受限")
        def g4():
            record = lisi.call("GET", f"/documents/{did}")
            objects = lisi.call("GET", f"/documents/{did}/objects")["objects"]
            ids = [item["id"] for item in objects]
            body = "\n\n".join(item["markdown"] for item in objects) + "\n"
            changed = body.replace("功能需求第二段：权限管理。", "功能需求第二段：李四改的。")
            response = lisi.raw("PATCH", f"/documents/{did}/content", json={"expectedRevision": record["revision"], "markdownContent": changed, "allowMerge": True, "blockIds": ids})
            assert response.status_code == 409 and response.json()["error"]["code"] == "SECTION_NOT_ASSIGNED", response.text
            details = response.json()["error"]["details"]
            assert details["total"] >= 1 and details["violations"][0]["owners"][0]["name"] == "王测", details
            response = lisi.raw("PATCH", f"/documents/{did}/content", json={"expectedRevision": record["revision"], "markdownContent": changed, "allowMerge": True})
            assert response.status_code == 422 and response.json()["error"]["code"] == "BLOCK_IDS_REQUIRED", response.text
            response = admin.raw("PATCH", f"/documents/{did}/content", json={"expectedRevision": record["revision"], "markdownContent": changed.replace("李四改的", "负责人改的"), "allowMerge": True, "blockIds": ids})
            assert response.status_code == 200, response.text
        g4()

        @step("G5 编写人员新建的文档整篇归创建人，其他编写人员只读")
        def g5():
            own = upload_doc(wangce, pid, "测试计划", make_word([("测试范围", ["王测写的测试范围。"])]))
            view = wangce.call("GET", f"/documents/{own['id']}/assignments")
            assert view["assignmentMode"] == "enforced"
            assert [(item["objectId"], item["assignees"][0]["name"]) for item in view["explicit"]] == [("@document", "王测")], view["explicit"]
            open_doc(page2, pid, own["id"])
            expect(para(page2, "王测写的测试范围")).to_have_attribute("data-assignee", "王测", timeout=15000)
            detail = lisi.call("GET", f"/documents/{own['id']}")
            assert detail["permissions"]["readonlyReason"] == "assignment", detail["permissions"]
        g5()

        @step("G6 编写人员在按分工编辑的文档上不能恢复版本")
        def g6():
            open_doc(page2, pid, did)
            page2.get_by_role("button", name="版本记录", exact=True).click()
            page2.locator(".history-panel .vitem").nth(1).click()
            restore = page2.get_by_role("button", name="恢复此版本")
            expect(restore).to_be_disabled(timeout=15000)
            expect(page2.locator(".history-hint")).to_contain_text("只有项目负责人能恢复版本")
            record = lisi.call("GET", f"/documents/{did}")
            response = lisi.raw("POST", f"/documents/{did}/versions/1/restore", json={"expectedRevision": record["revision"]})
            assert response.status_code == 403, response.text
            page2.get_by_role("button", name="版本记录", exact=True).click()
        g6()

        @step("G7 成员表“编写分工”列；点数字打开概述按人视图并定位")
        def g7():
            page.goto(f"{WEB}/projects/{pid}")
            row = page.locator(f'[data-testid=member-row][data-user-id="{users["lisi"]}"]')
            expect(row.get_by_test_id("member-assign-count")).to_contain_text("1 章", timeout=30000)
            expect(row.get_by_test_id("member-assign-count")).to_contain_text("1 篇文档")
            expect(page.locator(f'[data-testid=member-row][data-user-id="{users["liuyang"]}"]').get_by_test_id("member-assign-count")).to_have_text("—")
            row.get_by_test_id("member-assign-count").click()
            person = page.locator(f'[data-testid=overview-assign-person-row][data-user-id="{users["lisi"]}"]')
            expect(person).to_be_visible(timeout=15000)
            expect(person).to_contain_text("软件需求规格说明 · 1 章")
            expect(page.get_by_test_id("overview-assign-no-work")).to_contain_text("刘洋")
            shot(page, "A07-04-概述按人")
        g7()

        @step("G8 S-5：降为只读前确认（取消不改；确认后分工清除）")
        def g8():
            row = page.locator(f'[data-testid=member-row][data-user-id="{users["lisi"]}"]')
            row.get_by_test_id("member-role-select").select_option("viewer")
            dialog = page.get_by_test_id("member-demote-dialog")
            expect(dialog).to_be_visible()
            expect(dialog).to_contain_text("1 章将变为未分配")
            shot(page, "A07-05-降级确认")
            dialog.get_by_role("button", name="取消").click()
            expect(row.get_by_test_id("member-role-select")).to_have_value("editor")
            assert any(item["userId"] == str(users["lisi"]) and item["role"] == "editor" for item in admin.call("GET", f"/projects/{pid}/members")["items"])
            row.get_by_test_id("member-role-select").select_option("viewer")
            page.get_by_test_id("member-demote-confirm").click()
            expect(row.get_by_test_id("member-role-select")).to_have_value("viewer", timeout=15000)
            expect(row.get_by_test_id("member-assign-count")).to_have_text("—", timeout=15000)
            view = admin.call("GET", f"/documents/{did}/assignments")
            assert all(item["assignees"][0]["name"] != "李四" for item in view["explicit"]), view["explicit"]
        g8()

        @step("G9 概述“编写分工”按文档：已分 / 总章数，未分配可去分配")
        def g9():
            page.goto(f"{WEB}/projects/{pid}")
            section = page.get_by_test_id("overview-assign-section")
            section.get_by_role("button", name="按文档").click()
            doc_row = section.locator(f'[data-testid=overview-assign-doc-row][data-document-id="{did}"]')
            expect(doc_row).to_contain_text("1 / 3 章", timeout=15000)
            expect(section.locator('[data-testid=overview-assign-doc-row]', has_text="测试计划")).to_contain_text("整篇：王测")
            doc_row.get_by_test_id("overview-assign-goto").click()
            expect(page.get_by_test_id("assign-panel")).to_be_visible(timeout=60000)
            shot(page, "A07-06-概述去分配")
        g9()

        browser.close()
    failed = [item for item in results if not item[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过（{VIEW}）")
    for name, _ok, message in failed:
        print(f"  失败 {name}: {message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
