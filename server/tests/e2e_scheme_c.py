"""F-M3 方案 C 浏览器验收（14-最终界面验收 §3.6 八条 + “保留我的后半句丢失”复现 + T-M2）。

只打 E2E_API/E2E_WEB 指定的服务；DATABASE_URL 指向同一个库。环境变量：
  ONLY=场景名（逗号分隔，子串匹配）只跑部分场景；GAPS（时机覆盖的间隔毫秒，默认 0,150,300,600,1500）；
  N（时机覆盖每组轮数，默认 3）；BIG（时机覆盖另跑一组大文档的填充段落数，默认 0 不跑）。
冲突场景统一为：李工在第3段末尾打标记前 5 个字 → 张工改第3段 → 李工保存（Ctrl+S 或等自动保存）→ 李工接着打标记剩下的字。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_fallback as fb  # noqa: E402
import e2e_phase2 as base  # noqa: E402
import e2e_selfconf as selfconf  # noqa: E402

MARK = "【丙0123456789ABCDEFGHIJ丁】"  # 25 字
P3, P3_THEIRS = "第3段：原始内容 3。", "第3段：原始内容 3（对方改同段）。"
P3_AGAIN = "第3段：原始内容 3（对方又改）。"
rows: list[dict] = []


class Run:
    def __init__(self, data: dict, browser):
        self.data, self.browser = data, browser
        self.zhang = data["zhang"]
        self.li = base.Api(f"e2e-li-{base.RUN}")

    def page(self):
        context = self.browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN", accept_downloads=True)
        context.add_init_script(fb.INIT)
        page = context.new_page()
        base.open_pages[:] = [page]
        base.login(page, f"e2e-li-{base.RUN}")
        return page

    def doc(self, page, name: str, big: int = 0) -> str:
        selfconf.PARAS = [f"第{i}段：原始内容 {i}。" for i in range(1, 9)]
        doc_id = selfconf.make_doc(self.data, f"{name}-{time.strftime('%H%M%S')}", big)
        self.open(page, doc_id)
        return doc_id

    def open(self, page, doc_id: str) -> None:
        page.goto(f"{base.WEB}/projects/{self.data['project']['id']}/documents/{doc_id}")
        expect(page.locator(".paper-content")).to_be_visible(timeout=90000)
        base.wait_saved(page, 90000)
        page.wait_for_timeout(600)

    def server(self, doc_id: str) -> dict:
        return self.zhang.call("GET", f"/documents/{doc_id}")

    def zhang_edit(self, doc_id: str, old: str, new: str) -> None:
        fb.Ctx(self.data).zpatch(doc_id, lambda md: md.replace(old, new, 1))

    def items(self, doc_id: str, status: str = "open") -> list[dict]:
        return self.li.call("GET", f"/documents/{doc_id}/conflicts?status={status}&actor=me")["items"]

    def conflict_rows(self, doc_id: str) -> list[dict]:
        from app.db import connect
        with connect() as connection:
            got = connection.execute("SELECT id, status, payload_json FROM document_conflicts WHERE document_id=%s", (doc_id,)).fetchall()
        return [{"id": row["id"], "status": row["status"], "payload": json.loads(row["payload_json"])} for row in got]

    def make_conflict(self, page, doc_id: str, trigger: str = "manual", gap: int = 0, rest: str = MARK[5:], delay: int = 40) -> None:
        """李工打前 5 字 → 张工改同段 → 保存 → 隔 gap 毫秒打剩下的字；等到冻结区出现。"""
        base.type_after(page, "第3段", MARK[:5])
        self.zhang_edit(doc_id, P3, P3_THEIRS)
        if trigger == "manual":
            page.keyboard.press("Control+s")
        else:
            page.wait_for_function("window.__patches.length > 0 && window.__patches.some(p => p.status === 409)", timeout=30000)
        page.wait_for_timeout(gap)
        if rest:
            page.keyboard.type(rest, delay=delay)
        expect(page.locator(".paper-content [data-frozen]").first).to_be_visible(timeout=30000)

    def wait_snapshot(self, doc_id: str, text: str, timeout: float = 12.0) -> dict:
        deadline = time.time() + timeout
        while True:
            items = self.items(doc_id)
            hit = next((item for item in items if text in ((item.get("mineLatest") or {}).get("markdown") or "")), None)
            if hit or time.time() > deadline:
                return hit or (items[0] if items else {})
            time.sleep(0.5)



def open_mode(api, doc: dict) -> dict:
    """A07 之前写的场景假定编写人员能改整篇：新建文档（默认按分工编辑）改为不限制分工（open）。"""
    view = api.call("GET", f"/documents/{doc['id']}/assignments")
    if view["assignmentMode"] != "open":
        api.call("PUT", f"/documents/{doc['id']}/assignment-mode",
                 json={"mode": "open", "expectedAssignmentsRevision": view["assignmentsRevision"]})
    return doc

def scenario(name: str):
    def wrap(fn):
        def run(ctx: Run):
            only = [item for item in os.environ.get("ONLY", "").split(",") if item]
            if only and not any(item in name for item in only):
                return
            page = None
            try:
                page = ctx.page()
                detail = fn(ctx, page) or {}
                rows.append({"name": name, "ok": True, **detail})
                print(f"PASS {name} {json.dumps(detail, ensure_ascii=False)[:400]}", flush=True)
            except Exception as error:  # noqa: BLE001 - 汇总
                rows.append({"name": name, "ok": False, "error": f"{type(error).__name__}: {str(error)[:400]}"})
                print(f"FAIL {name}: {type(error).__name__}: {str(error)[:600]}", flush=True)
                if page and not page.is_closed():
                    base.OUT.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(base.OUT / f"schemec-fail-{re.sub(r'[^0-9A-Za-z]+', '_', name)}.png"))
            finally:
                if page and not page.is_closed():
                    page.context.close()
        return run
    return wrap


def card(page):
    return page.locator(".conflict-panel .ccard").first


def open_panel(page):
    if not page.locator(".conflict-panel").count():
        page.get_by_test_id("conflict-toggle").click()
    expect(card(page)).to_be_visible(timeout=20000)


def frozen_text(page) -> str:
    return "\n".join(page.locator(".paper-content [data-frozen]").all_inner_texts())


def assert_no_self_named(page) -> None:
    body = page.locator("body").inner_text()
    for bad in ("李工 又修改了一次", "李工 在你编辑期间保存了这一段", "与 李工 的修改冲突"):
        assert bad not in body, f"界面出现“{bad}”"


# —— 复现：保留我的后半句丢失（efb41bc 上第一次点 409 当成对方、再点丢后半句）——
@scenario("复现 保留我的后半句不丢")
def s_repro(ctx: Run, page):
    doc_id = ctx.doc(page, "复现保留我的")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    page.wait_for_timeout(1500)
    md = ctx.server(doc_id)["markdownContent"]
    first_ok = not page.locator(".conflict-panel .ccard-notice").count()
    assert MARK in md, f"正文缺完整标记：{md[:300]}"
    assert P3_THEIRS not in md
    assert first_ok, "第一次点“保留我的”就出现了再次冲突提示"
    assert not ctx.items(doc_id), "冲突仍未处理"
    return {"server_has_mark": True}


# —— 1. 时机覆盖 ——
def timing_rows(ctx: Run, page, big: int) -> list[dict]:
    out = []
    gaps = [int(item) for item in os.environ.get("GAPS", "0,150,300,600,1500").split(",")]
    for gap in gaps:
        for i in range(int(os.environ.get("N", "3"))):
            doc_id = ctx.doc(page, f"方案C时机{'大' if big else ''}{gap}-{i}", big)
            ctx.make_conflict(page, doc_id, "manual", gap, delay=60)
            hit = ctx.wait_snapshot(doc_id, MARK, 12 if not big else 30)
            md = ctx.server(doc_id)["markdownContent"]
            block = next((b for b in md.split("\n\n") if b.startswith("第3段")), "")
            row = {"gap": gap, "i": i, "big": big, "frozen_full": MARK in frozen_text(page), "snapshot_full": MARK in ((hit.get("mineLatest") or {}).get("markdown") or ""),
                   "body_is_theirs": block == P3_THEIRS, "mark_in_body": md.count(MARK[:3]) + md.count(MARK[-3:])}
            row["ok"] = row["frozen_full"] and row["snapshot_full"] and row["body_is_theirs"] and row["mark_in_body"] == 0
            out.append(row)
            print("  " + json.dumps(row, ensure_ascii=False), flush=True)
    return out


@scenario("1 时机覆盖 小文档")
def s_timing_small(ctx: Run, page):
    got = timing_rows(ctx, page, 0)
    bad = [row for row in got if not row["ok"]]
    assert not bad, f"{len(bad)}/{len(got)} 轮不通过：{bad[:3]}"
    return {"rounds": len(got)}


@scenario("1 时机覆盖 大文档")
def s_timing_big(ctx: Run, page):
    big = int(os.environ.get("BIG", "0"))
    if not big:
        return {"skipped": "BIG=0"}
    got = timing_rows(ctx, page, big)
    bad = [row for row in got if not row["ok"]]
    assert not bad, f"{len(bad)}/{len(got)} 轮不通过：{bad[:3]}"
    return {"rounds": len(got)}


# —— 2. 冲突返回时节点不换、光标不动；组字不被打断 ——
WATCH = r"""
(() => {
  const root = document.querySelector('.paper-content');
  const p = [...root.children].find(n => n.textContent.startsWith('第3段'));
  window.__p3 = p; window.__p3Removed = 0;
  new MutationObserver(list => { for (const m of list) for (const n of m.removedNodes) if (n === p) window.__p3Removed++; }).observe(root, { childList: true });
})()
"""
CARET = "(() => { const s = getSelection(); const p = window.__p3; return { inP: !!(s.anchorNode && p.contains(s.anchorNode)), off: s.anchorOffset, len: (s.anchorNode && s.anchorNode.textContent || '').length }; })()"


@scenario("2 节点不换 光标不动 组字不打断")
def s_dom(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C节点")
    base.type_after(page, "第3段", MARK[:5])
    page.evaluate(WATCH)
    ctx.zhang_edit(doc_id, P3, P3_THEIRS)
    page.evaluate("window.__hold = true")
    page.keyboard.press("Control+s")
    page.wait_for_function("window.__patches.length > 0", timeout=10000)
    before = page.evaluate(CARET)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.imeSetComposition", {"text": "zhong", "selectionStart": 5, "selectionEnd": 5})
    page.evaluate("window.__hold = false")
    expect(page.locator(".paper-content [data-frozen]").first).to_be_visible(timeout=20000)
    page.wait_for_timeout(1500)
    mid = page.evaluate(CARET)
    cdp.send("Input.insertText", {"text": "中"})
    page.wait_for_timeout(500)
    same_node = page.evaluate("document.querySelector('.paper-content [data-frozen]') === window.__p3")
    removed = page.evaluate("window.__p3Removed")
    text = frozen_text(page)
    assert same_node and removed == 0, f"冻结块节点被替换/移除（same={same_node} removed={removed}）"
    assert before["inP"] and mid["inP"], f"光标离开了第3段：{before} → {mid}"
    assert "中" in text and "zhong" not in text, f"组字被打断：{text}"
    assert MARK[:5] + "中" in text
    hit = ctx.wait_snapshot(doc_id, MARK[:5] + "中")
    assert MARK[:5] + "中" in hit["mineLatest"]["markdown"]
    return {"caret_before": before, "caret_mid": mid}


# —— 3. 冲突后在其他段输入 ——
@scenario("3 冲突后其他段照常保存")
def s_other(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C其他段")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    before_rows = ctx.conflict_rows(doc_id)
    rev = ctx.server(doc_id)["revision"]
    base.type_after(page, "第6段", "〔其他段〕")
    page.keyboard.press("Control+s")
    base.wait_saved(page, 30000)
    page.wait_for_timeout(1000)
    base.type_after(page, "第3段", "〔冻结区续写〕")
    base.type_after(page, "第7段", "〔再一段〕")
    page.wait_for_timeout(5000)
    base.wait_saved(page, 30000)
    server = ctx.server(doc_id)
    after_rows = ctx.conflict_rows(doc_id)
    attempts = [item.get("attempt", 1) for row in after_rows for item in row["payload"].get("conflicts", [])]
    assert "〔其他段〕" in server["markdownContent"] and "〔再一段〕" in server["markdownContent"]
    assert "〔冻结区续写〕" not in server["markdownContent"]
    assert server["revision"] > rev
    assert len(after_rows) == len(before_rows) == 1, f"冲突行数 {len(before_rows)} → {len(after_rows)}"
    assert not [row for row in after_rows if row["status"] == "superseded"]
    assert attempts == [1], attempts
    assert "〔冻结区续写〕" in ctx.wait_snapshot(doc_id, "〔冻结区续写〕")["mineLatest"]["markdown"]
    return {"revision": [rev, server["revision"]]}


# —— 4. 四种处理结果 ——
@scenario("4a 保留我的 含回车分段")
def s_keep_mine(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C保留我的")
    ctx.make_conflict(page, doc_id, "manual", 300)
    page.keyboard.press("Enter")
    page.keyboard.type("〔回车分出的段〕", delay=30)
    ctx.wait_snapshot(doc_id, "〔回车分出的段〕")
    open_panel(page)
    expect(card(page).get_by_test_id("conflict-mine-status")).to_contain_text("含冲突后续写的")
    expect(card(page).get_by_test_id("conflict-mine-status")).to_contain_text("已存为冲突副本")
    card(page).get_by_role("button", name="保留我的").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    expect(page.locator(".wb-notice", has_text="已用你的版本（含续写）替换正文里的这一段")).to_be_visible(timeout=5000)
    assert not page.locator(".conflict-panel .ccard-notice").count(), "第一次点就要求重新确认"
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    blocks = md.split("\n\n")
    assert P3 + MARK in blocks and "〔回车分出的段〕" in blocks, md[:400]
    assert P3_THEIRS not in md and md.count(MARK) == 1, md[:400]
    editor = page.locator(".paper-content").inner_text()
    assert MARK in editor and "〔回车分出的段〕" in editor
    return {}


@scenario("4b 使用对方的版本 已处理可下载最新")
def s_use_theirs(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C使用对方")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    card(page).get_by_role("button", name="使用对方的版本").click()
    confirm = card(page).locator(".ccard-confirm p")
    expect(confirm).to_have_text("编辑区里这一段会换成 张工 的版本。你的版本（含后来续写的内容）仍保存在冲突记录中，可在「已处理」里查看和下载。")
    card(page).locator(".ccard-confirm").get_by_role("button", name="使用对方的版本").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    expect(page.locator(".paper-content p", has_text="第3段")).to_have_text(P3_THEIRS, timeout=20000)
    page.wait_for_timeout(3500)
    base.wait_saved(page, 30000)
    md = ctx.server(doc_id)["markdownContent"]
    block = next(b for b in md.split("\n\n") if b.startswith("第3段"))
    assert block == P3_THEIRS, f"使用对方的版本后留下了半句：{block}"
    assert MARK[5:12] not in md and MARK[:5] not in md, md[:400]
    handled = page.locator(".conflict-handled")
    expect(handled).to_be_visible(timeout=10000)
    if not handled.evaluate("e => e.open"):
        handled.locator("summary").click()
    with page.expect_download() as info:
        handled.get_by_role("button", name="下载我的副本").first.click()
    path = info.value.path()
    text = open(path, encoding="utf-8").read()
    resolved = ctx.items(doc_id, "resolved")
    assert MARK in ((resolved[0].get("mineLatest") or {}).get("markdown") or ""), resolved
    return {"download_has_full_mark": MARK in text, "download_len": len(text)}


@scenario("4c 两段都保留 顺序对方在前")
def s_keep_both(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C两段都保留")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    card(page).get_by_role("button", name="两段都保留").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    expect(page.locator(".wb-notice", has_text="已在你的版本前插入 张工 的版本，两段都在正文中。")).to_be_visible(timeout=5000)
    md = ctx.server(doc_id)["markdownContent"]
    blocks = md.split("\n\n")
    assert P3_THEIRS in blocks and P3 + MARK in blocks, f"两段不完整：{blocks}"
    i_theirs, i_mine = blocks.index(P3_THEIRS), blocks.index(P3 + MARK)
    assert i_mine == i_theirs + 1, f"顺序不对：对方在第 {i_theirs} 块、我的完整版在第 {i_mine} 块"
    page.wait_for_timeout(4000)
    base.wait_saved(page, 30000)
    md2 = ctx.server(doc_id)["markdownContent"]
    assert md2.count(P3_THEIRS) == 1 and md2.count(MARK) == 1, md2[:400]
    return {}


@scenario("4d 手动合并 预填对方 插入最新我的")
def s_manual(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C手动合并")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    card(page).get_by_role("button", name="手动合并").click()
    area = card(page).locator("textarea")
    expect(area).to_have_value(P3_THEIRS)
    base.type_after(page, "第3段", "〔后来又写〕")
    page.wait_for_timeout(300)
    card(page).get_by_role("button", name="插入我的版本").click()
    value = area.input_value()
    assert MARK in value and "〔后来又写〕" in value, value
    card(page).get_by_role("button", name="采用合并结果").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    assert not page.locator(".conflict-panel .ccard-notice").count(), "第一次采用就要求重新确认"
    md = ctx.server(doc_id)["markdownContent"]
    assert "〔后来又写〕" in md and P3_THEIRS in md and MARK in md and md.count(MARK) == 1, md[:400]
    return {}


# —— 5. 离开与刷新 ——
@scenario("5 离开不弹框 断网计入 重开是最新快照")
def s_leave(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C离开")
    ctx.make_conflict(page, doc_id, "auto", 300)
    ctx.wait_snapshot(doc_id, MARK)
    base.wait_saved(page, 30000)
    page.get_by_role("button", name="返回文档列表").click()
    expect(page.get_by_text("1 处冲突已保存在服务器（含你的续写），可稍后在本文档的“冲突”中处理。")).to_be_visible(timeout=10000)
    assert not page.get_by_role("alertdialog").count()
    # 重开：卡片里的我的版本是最新快照
    ctx.open(page, doc_id)
    open_panel(page)
    expect(card(page).get_by_test_id("conflict-reopen-note")).to_have_text("重新打开后，编辑区显示的是正文里的版本；你的版本在这张卡片里。")
    expect(card(page).locator(".ccard-side.mine")).to_contain_text(MARK)
    assert P3_THEIRS in page.locator(".paper-content").inner_text()
    # 断网续写后离开：对话框计入 1 处
    doc2 = ctx.doc(page, "方案C断网离开")
    ctx.make_conflict(page, doc2, "manual", 300)
    ctx.wait_snapshot(doc2, MARK)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    base.type_after(page, "第3段", "〔断网续写〕")
    page.wait_for_timeout(800)
    page.get_by_role("button", name="返回文档列表").click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_be_visible(timeout=20000)
    expect(dialog).to_contain_text("1 处")
    base.shot(page, "C5-断网离开")
    page.get_by_role("button", name="留在本页").click()
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    hit = ctx.wait_snapshot(doc2, "〔断网续写〕", 40)
    assert "〔断网续写〕" in hit["mineLatest"]["markdown"]
    return {}


@scenario("5b 刷新 续写由本机草稿兜底")
def s_reload_draft(ctx: Run, page):
    """审查点 5：快照存不上（接口失败）时刷新页面，冻结区续写从本机草稿补发为快照，不丢字。"""
    doc_id = ctx.doc(page, "方案C刷新兜底")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    page.route("**/conflicts/*/mine", lambda route: route.abort())
    base.type_after(page, "第3段", "〔草稿兜底〕")
    page.wait_for_timeout(4500)
    assert "〔草稿兜底〕" not in (ctx.items(doc_id)[0].get("mineLatest") or {}).get("markdown", "")
    page.unroute("**/conflicts/*/mine")
    page.reload()
    expect(page.locator(".paper-content")).to_be_visible(timeout=90000)
    hit = ctx.wait_snapshot(doc_id, "〔草稿兜底〕", 40)
    assert "〔草稿兜底〕" in hit["mineLatest"]["markdown"] and MARK in hit["mineLatest"]["markdown"]
    assert "〔草稿兜底〕" not in ctx.server(doc_id)["markdownContent"]
    return {}


# —— 6. 处理时张工又改 ——
@scenario("6 再次冲突 黄条写张工")
def s_reconflict(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C再次冲突")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    expect(card(page).get_by_test_id("conflict-frozen-desc")).to_have_text("张工 在你编辑期间保存了这一段。你的版本还在编辑区（红框内），可以继续写；处理前不会写入正文。")
    ctx.zhang_edit(doc_id, P3_THEIRS, P3_AGAIN)
    card(page).get_by_role("button", name="保留我的").click()
    expect(card(page).locator(".ccard-notice")).to_have_text("张工 又修改了一次，请重新确认。", timeout=20000)
    assert_no_self_named(page)
    attempts = [item.get("attempt") for item in ctx.items(doc_id)]
    card(page).get_by_role("button", name="保留我的").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    md = ctx.server(doc_id)["markdownContent"]
    assert MARK in md and P3_AGAIN not in md
    assert_no_self_named(page)
    return {"attempts": attempts}


# —— 7. base 不等于本人提交过的文字（整合进各场景之后统一检查）——
def check_bases(ctx: Run) -> dict:
    from app.db import connect
    with connect() as connection:
        got = connection.execute("SELECT c.payload_json FROM document_conflicts c JOIN documents d ON d.id=c.document_id "
                                 "JOIN users u ON u.id=c.actor_id WHERE d.name LIKE '方案C%%' AND u.name='李工'").fetchall()
    bases = [((item.get("base") or {}).get("markdown") or "") for row in got for item in json.loads(row["payload_json"]).get("conflicts", [])]
    bad = [b for b in bases if any(part in b for part in ("【丙", "〔", "中"))]
    return {"conflicts": len(bases), "self_base": bad[:3]}


# —— 8. 文案（散在各场景；这里补自动保存提示条、正文标记、标签、删光、AI、导出）——
@scenario("8 文案 自动保存提示条 标记 标签")
def s_copy(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C文案")
    ctx.make_conflict(page, doc_id, "auto", 300)
    banner = page.get_by_test_id("auto-conflict")
    expect(banner).to_contain_text("这一段和 张工 的修改冲突。你可以继续写，写的内容先存为冲突副本，不会丢失；处理后才写入正文。")
    expect(banner.get_by_role("button", name="查看冲突")).to_be_visible()
    mark = page.get_by_test_id("conflict-mark").first
    expect(mark).to_have_text("未保存 · 与 张工 的修改冲突")
    assert mark.get_attribute("title") == "这一段和 张工 的修改冲突。你在这里写的内容先存为冲突副本，不会丢失，也暂不写入正文；在冲突面板处理后才写入。"
    ctx.wait_snapshot(doc_id, MARK)
    base.shot(page, "C8-自动保存冲突-冻结区")
    banner.get_by_role("button", name="查看冲突").click()
    expect(card(page)).to_be_visible(timeout=10000)
    expect(card(page).locator(".ccard-side.theirs h4")).to_contain_text("张工 的版本 · 已在正文中（其他人看到的是这一版）")
    expect(card(page).locator(".ccard-side.mine h4")).to_contain_text("我的版本 · 在你的编辑区，未写入正文")
    expect(card(page).get_by_test_id("conflict-mine-status")).to_have_text(re.compile(r"^含冲突后续写的 19 字 · 已存为冲突副本 \d\d:\d\d$"))
    base.shot(page, "C8-冲突卡片")
    # 把冻结区文字全删掉：卡片写删光，只剩两个按钮
    region = page.locator(".paper-content [data-frozen]").first
    region.click()
    page.keyboard.press("End")
    page.keyboard.press("Shift+Home")
    page.keyboard.press("Delete")
    page.wait_for_timeout(4500)
    mine = card(page).locator(".ccard-side.mine")
    expect(mine).to_contain_text("（你在编辑区删除了这一段的全部文字）", timeout=10000)
    labels = [text.strip() for text in card(page).locator(".choices button").all_inner_texts()]
    assert sorted(labels) == sorted(["使用对方的版本", "手动合并"]), labels
    base.shot(page, "C8-删光冻结区")
    return {"buttons": labels}


@scenario("8 文案 导出与 AI")
def s_copy_export_ai(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C导出")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    page.keyboard.press("Escape")
    page.get_by_test_id("export-word").click()
    expect(page.get_by_text("有 1 处冲突未处理，导出的是正文里的版本（不含你在冲突段里的修改）。")).to_be_visible(timeout=10000)
    return {}


# —— T-M2：他人改了光标所在段，不自动刷新 ——
@scenario("T-M2 光标段被他人更新不自动刷新")
def s_tm2(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C光标段")
    fb.click(page, "第5段")
    ctx.zhang_edit(doc_id, "第5段：原始内容 5。", "第5段：原始内容 5（张工改）。")
    expect(page.get_by_test_id("others-here")).to_have_text("张工 更新了你正在看的这一段。", timeout=60000)  # 每 45 秒查一次
    assert "（张工改）" not in page.locator(".paper-content").inner_text()
    top = page.locator(".paper-content").bounding_box()["y"]
    base.shot(page, "TM2-光标段更新")
    page.get_by_test_id("others-updated").get_by_role("button", name="立即刷新").click()
    expect(page.locator(".paper-content p", has_text="第5段")).to_contain_text("（张工改）", timeout=20000)
    return {"editor_top": top}


# —— F-M4：表格、列表里冲突后继续打的字不丢 ——
TABLE = "| 项目 | 说明 |\n| --- | --- |\n| 甲 | 单元格甲 |\n| 乙 | 单元格乙 |\n| 丙 | 单元格丙 |"
LIST = "- 列表项一\n- 列表项二\n- 列表项三"
TMARK = "【表0123456789ABCDEFGHIJ尾】"
CASES = {"表格同一行": ("td", "单元格甲", ("| 甲 |", "| 甲（对方改） |")), "表格另一行": ("td", "单元格甲", ("单元格丙", "单元格丙（对方改）")),
         "列表同一项": ("li", "列表项一", ("列表项一", "列表项一（对方改）"))}


def tablist_rows(ctx: Run, page) -> list[dict]:
    out = []
    gaps = [int(item) for item in os.environ.get("TGAPS", "0,600,1500").split(",")]
    pick = [item for item in os.environ.get("TCASES", "").split(",") if item]
    for case, (tag, anchor, (old, new)) in CASES.items():
        if pick and not any(item in case for item in pick):
            continue
        for gap in gaps:
            for i in range(int(os.environ.get("TN", "3"))):
                selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
                doc_id = selfconf.make_doc(ctx.data, f"方案C{case}{gap}-{i}-{time.strftime('%H%M%S')}", 0)
                ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + TABLE + "\n\n" + LIST)
                ctx.open(page, doc_id)
                target = page.locator(f".paper-content {tag}", has_text=anchor).first
                target.scroll_into_view_if_needed()
                target.click()
                page.keyboard.press("End")
                page.keyboard.type(TMARK[:5])
                ctx.zhang_edit(doc_id, old, new)
                page.keyboard.press("Control+s")
                page.wait_for_timeout(gap)
                page.keyboard.type(TMARK[5:-2], delay=150)
                page.keyboard.insert_text(TMARK[-2:])  # 不产生 keydown 的直接上屏
                page.wait_for_timeout(5000)
                try:
                    base.wait_saved(page, 20000)
                except Exception:  # noqa: BLE001 - 有未处理冲突时状态不是“已保存”
                    pass
                md = ctx.server(doc_id)["markdownContent"]
                items = ctx.items(doc_id)
                latest = "\n".join(((item.get("mineLatest") or {}).get("markdown") or (item.get("mine") or {}).get("markdown") or "") for item in items)
                editor = page.locator(".paper-content").inner_text()
                frozen = page.locator(".paper-content [data-frozen]").count()
                body = page.locator("body").inner_text()
                row = {"case": case, "gap": gap, "i": i, "server_full": TMARK in md, "conflict_full": TMARK in latest, "editor_full": TMARK in editor.replace("\t", ""),
                       "frozen": frozen, "other_kept": new in md, "dup": md.count(TMARK[:3]) + latest.count(TMARK[:3]) > 1 and TMARK not in md,
                       "lost_notice": "没有写入" in body, "sink_note": page.locator("[data-testid=sink-note]").count()}
                row["ok"] = (row["server_full"] or row["conflict_full"]) and row["other_kept"] and not row["lost_notice"]
                out.append(row)
                print("  " + json.dumps(row, ensure_ascii=False), flush=True)
                if not row["ok"]:
                    base.OUT.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(base.OUT / f"schemec-fm4-{case}-{gap}-{i}.png"))
    return out


@scenario("F-M4 表格列表 冲突后续打不丢")
def s_tablist(ctx: Run, page):
    got = tablist_rows(ctx, page)
    bad = [row for row in got if not row["ok"]]
    assert not bad, f"{len(bad)}/{len(got)} 轮不通过：{bad[:3]}"
    return {"rounds": len(got), "frozen": sum(1 for row in got if row["frozen"])}


# —— F-L1 缺口：载入锁定期间不经按键的直接上屏 / 组字 ——
STRETCH_LOCK = """(() => { const raw = window.setTimeout; window.setTimeout = (fn, ms, ...rest) => raw(fn, ms === 250 || (ms > 0 && ms < 250 && typeof fn === 'function' && String(fn).includes('[native code]')) ? 2500 : ms, ...rest); })()"""


def load_lock_round(ctx: Run, page, mode: str, n: int) -> dict:
    """载入锁定窗口真实只有约 250ms（打开时等其他标签回报的 PRESENCE_WAIT_MS），测试里把这一个 250ms 等待拉长到 2.5s 以稳定打中。"""
    mark = f"【载入{mode}{n}】"
    if not getattr(page, "_stretched", False):
        page.context.add_init_script(STRETCH_LOCK)
        page._stretched = True
    for attempt in range(6):
        selfconf.PARAS = [f"第{i}段：原始内容 {i}。" for i in range(1, 9)]
        doc_id = selfconf.make_doc(ctx.data, f"方案C载入锁定{mode}{n}-{attempt}-{time.strftime('%H%M%S')}", 0)
        # goto 默认等 load 事件，那时锁定早已结束：只等到提交导航
        page.goto(f"{base.WEB}/projects/{ctx.data['project']['id']}/documents/{doc_id}", wait_until="commit")
        try:
            page.wait_for_selector(".paper-content[contenteditable='false'] p", state="attached", timeout=30000)
        except Exception:  # noqa: BLE001 - 锁定窗口太短没等到：这次不算，重来
            print(f"  {mode}{n} attempt {attempt}: 没等到锁定窗口", flush=True)
            continue
        # 不用 locator.click()：它要等元素“可操作”，会一直等到解锁；按坐标直接点（与真人一样）
        box = page.locator(".paper-content p", has_text="第2段").first.bounding_box()
        if not box:
            continue
        page.mouse.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
        if not page.locator("[data-testid=load-sink]").count():
            print(f"  {mode}{n} attempt {attempt}: 点下去时已解锁", flush=True)
            continue  # 点下去时已经解锁：这次没打中锁定窗口，重来
        if mode == "insert":
            page.keyboard.insert_text(mark)
        elif mode == "ime":
            cdp = page.context.new_cdp_session(page)
            cdp.send("Input.imeSetComposition", {"text": "zairu", "selectionStart": 5, "selectionEnd": 5})
            page.wait_for_timeout(120)
            # 设计师 S4：组字中的拼音不计入“已记下 N 个字”；框可见
            held = page.evaluate("() => { const s = document.querySelector('[data-testid=load-sink]'); return s ? [s.dataset.held, getComputedStyle(s).opacity, s.getBoundingClientRect().width] : null }")
            if held is not None:
                assert held[0] == "0", f"组字中的拼音被计数：{held}"
                assert held[1] == "1" and held[2] > 100, f"载入期间的输入框不可见：{held}"
            cdp.send("Input.insertText", {"text": mark})
        else:
            page.keyboard.type(mark)
        page.wait_for_timeout(500)
        base.wait_saved(page, 30000)
        page.wait_for_timeout(3500)
        base.wait_saved(page, 30000)
        md = ctx.server(doc_id)["markdownContent"]
        block = next((b for b in md.split("\n\n") if b.startswith("第2段") or mark in b), "")
        notes = page.locator("body").inner_text()
        return {"mode": mode, "n": n, "attempt": attempt, "in_p2": mark in block and block.count(mark) == 1, "dup": md.count(mark) > 1,
                "sink_left": page.locator("[data-testid=load-sink]").count(), "lost_note": "没能写入" in notes}
    return {"mode": mode, "n": n, "attempt": "miss"}


@scenario("F-L1 载入锁定 直接上屏和组字不丢")
def s_load_lock(ctx: Run, page):
    got = [load_lock_round(ctx, page, mode, n) for mode in ("insert", "ime", "type") for n in range(int(os.environ.get("LN", "3")))]
    for row in got:
        print("  " + json.dumps(row, ensure_ascii=False), flush=True)
    hits = [row for row in got if row["attempt"] != "miss"]
    bad = [row for row in hits if not row["in_p2"] or row["dup"] or row["sink_left"]]
    assert hits, "一次也没打中载入锁定窗口"
    assert not bad, f"{len(bad)}/{len(hits)} 轮不通过：{bad[:3]}"
    return {"hits": len(hits), "rounds": len(got)}


# —— J2-L：“对方”其实是我自己（另一个标签页 / 另一份草稿先写进了正文）——
@scenario("J2-L 自己另一标签页 不显示为对方")
def s_self_theirs(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C自己两标签")
    other = page.context.new_page()
    other.goto(page.url)
    expect(other.locator(".paper-content")).to_be_visible(timeout=90000)
    base.wait_saved(other, 90000)
    other.wait_for_timeout(600)
    base.type_after(other, "第3段", "〔标签乙〕")
    other.keyboard.press("Control+s")
    base.wait_saved(other, 30000)
    other.close()
    base.type_after(page, "第3段", "〔标签甲〕")
    page.keyboard.press("Control+s")
    page.wait_for_timeout(2500)
    items = ctx.items(doc_id)
    if not items:
        md = ctx.server(doc_id)["markdownContent"]
        assert "〔标签甲〕" in md and "〔标签乙〕" in md, md[:400]
        return {"conflict": False, "merged": True}
    open_panel(page)
    text = page.locator(".conflict-panel").inner_text() + "\n" + page.locator(".paper-content").evaluate("e => e.parentElement.innerText")
    assert "李工 的版本" not in text and "与 李工 的修改冲突" not in text, text[:600]
    assert "你在另一处保存的版本 · 已在正文中" in text and "你在另一个标签页（或恢复的草稿）里保存了这一段" in text, text[:600]
    assert "与你在另一处的修改冲突" in text, text[-300:]
    assert_no_self_named(page)
    return {"conflict": True}


# —— 设计师必改 1、2：列表项换成对方版本那一刻就有常驻提示；之后多批续写都在“我的版本”里 ——
@scenario("列表项 换版即提示 多批续写不互相覆盖")
def s_list_batches(ctx: Run, page):
    selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
    doc_id = selfconf.make_doc(ctx.data, f"方案C列表多批-{time.strftime('%H%M%S')}", 0)
    ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + LIST)
    ctx.open(page, doc_id)
    target = page.locator(".paper-content li", has_text="列表项一").first
    target.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    ctx.zhang_edit(doc_id, "列表项一", "列表项一（对方改）")
    page.keyboard.press("Control+s")
    page.keyboard.type("，续零】", delay=150)  # 409 返回时还在编辑区里打
    note = page.get_by_test_id("sink-note")
    expect(note).to_be_visible(timeout=15000)
    first_note = note.inner_text()
    expect(page.locator(".paper-content li", has_text="列表项一（对方改）")).to_be_visible(timeout=20000)
    page.wait_for_timeout(1500)
    batches = ["〔续一〕", "〔续二〕", "〔续三〕"]
    for text in batches:
        page.keyboard.type(text, delay=60)
        page.wait_for_timeout(2500)
    deadline = time.time() + 15
    while True:
        latest = "\n".join((item.get("mineLatest") or {}).get("markdown") or "" for item in ctx.items(doc_id))
        if all(text in latest for text in batches) or time.time() > deadline:
            break
        time.sleep(0.5)
    missing = [text for text in ["【我改的", *batches] if text not in latest]
    assert not missing, f"我的版本缺 {missing}：{latest}"
    assert "列表项一（对方改）" in ctx.server(doc_id)["markdownContent"]
    open_panel(page)
    mine_card = card(page).locator(".ccard-side.mine").inner_text()
    assert batches[-1] in mine_card, f"冲突卡未刷新：{mine_card}"
    return {"first_note": first_note[:60], "latest": latest[-80:]}


# —— 设计师必改 3：断网续写后关页重开，不出现幽灵草稿条、编辑区不锁 ——
@scenario("断网续写关页重开 无幽灵草稿条")
def s_offline_reopen(ctx: Run, page):
    doc_id = ctx.doc(page, "方案C断网关页")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    base.type_after(page, "第3段", "〔断网关页续写〕")
    page.wait_for_timeout(1500)
    url = page.url
    other = page.context.new_page()
    page.close(run_before_unload=False)
    other.context.set_offline(False)
    other.goto(url)
    expect(other.locator(".paper-content")).to_be_visible(timeout=90000)
    hit = ctx.wait_snapshot(doc_id, "〔断网关页续写〕", 30)
    assert "〔断网关页续写〕" in ((hit.get("mineLatest") or {}).get("markdown") or ""), hit
    other.wait_for_timeout(3000)
    assert not other.get_by_test_id("draft-restore").count(), "出现了幽灵草稿条"
    assert other.locator(".paper-content").get_attribute("contenteditable") == "true", "编辑区被锁"
    other.close()
    return {}


@scenario("M1 另一标签快照后 保留我的 确认后按最新一份")
def s_other_tab_keep_mine(ctx: Run, page):
    """审查员 M1（p9-c-probe A3）：同一人另一个标签页追加了快照，本标签点“保留我的”不能永远 409；
    展示最新一份（注明另一个标签页），再点一次按这一份处理，另一个标签页写的字不丢。"""
    doc_id = ctx.doc(page, "方案C另一标签快照")
    ctx.make_conflict(page, doc_id, "manual", 300)
    hit = ctx.wait_snapshot(doc_id, MARK)
    other = P3 + MARK + "〔另一标签写的〕"
    ctx.li.call("POST", f"/documents/{doc_id}/conflicts/{hit['conflictId']}/mine", json={
        "itemIndex": hit["itemIndex"], "markdown": other, "clientSaveKey": str(uuid.uuid4()), "tabId": "other-tab", "seq": int(time.time() * 1000)})
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    notice = card(page).locator(".ccard-notice")
    expect(notice).to_contain_text("你自己在另一个标签页", timeout=20000)
    expect(notice).to_contain_text("再点一次「保留我的」")
    expect(card(page)).to_contain_text("〔另一标签写的〕")
    assert_no_self_named(page)
    card(page).get_by_role("button", name="保留我的").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    assert other in md.split("\n\n"), md[:400]
    expect(page.locator(".paper-content")).to_contain_text("〔另一标签写的〕")
    return {}


@scenario("A3c 另一标签快照后本标签又续写 处理前确认 手动合并两份都在")
def s_other_tab_then_mine(ctx: Run, page):
    """审查建议 1（A3c，已定）：另一个标签页写了快照（含〔乙的字〕）后，本标签又在冻结段续写，最新一份没有乙的字。
    点“保留我的”不能静默丢：弹确认、并列两份；默认手动合并两份都预填；采用后正文里两边的字都在。"""
    doc_id = ctx.doc(page, "方案C另一标签后本标签续写")
    ctx.make_conflict(page, doc_id, "manual", 300)
    hit = ctx.wait_snapshot(doc_id, MARK)
    ctx.li.call("POST", f"/documents/{doc_id}/conflicts/{hit['conflictId']}/mine", json={
        "itemIndex": hit["itemIndex"], "markdown": P3 + MARK + "〔乙的字〕", "clientSaveKey": str(uuid.uuid4()), "tabId": "other-tab", "seq": int(time.time() * 1000)})
    base.type_after(page, MARK, "〔甲后写〕")
    latest = ctx.wait_snapshot(doc_id, "〔甲后写〕")
    assert "〔乙的字〕" not in ((latest.get("mineLatest") or {}).get("markdown") or ""), "前提不成立：最新一份已含乙的字"
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    confirm = card(page).get_by_test_id("others-confirm")
    expect(confirm).to_be_visible(timeout=20000)
    expect(confirm.get_by_test_id("others-text")).to_contain_text("〔乙的字〕")
    expect(confirm).to_contain_text("〔甲后写〕")
    assert ctx.items(doc_id), "确认前就处理掉了"
    # 设计师复验 2 S-C2：焦点进确认框主按钮；读屏按确认文字朗读；文案说“点「手动合并」会把两份都放进合并框”
    expect(confirm.get_by_role("button", name="手动合并（两份都放进去）")).to_be_focused(timeout=5000)
    labelled = confirm.evaluate("el => document.getElementById(el.getAttribute('aria-labelledby') || '')?.textContent || ''")
    assert "另一个标签页" in labelled, labelled
    expect(confirm).to_contain_text("会把两份都放进合并框")
    confirm.get_by_role("button", name="手动合并（两份都放进去）").click()
    area = card(page).locator("textarea")
    value = area.input_value()
    assert "〔甲后写〕" in value and "〔乙的字〕" in value, value
    card(page).get_by_role("button", name="采用合并结果").click()
    deadline = time.time() + 20
    while ctx.items(doc_id) and time.time() < deadline:
        time.sleep(0.5)
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    assert "〔甲后写〕" in md and "〔乙的字〕" in md, md[:500]
    expect(page.locator(".paper-content")).to_contain_text("〔乙的字〕", timeout=15000)
    assert_no_self_named(page)
    return {}


@scenario("A3c 只有本标签写过 不弹确认")
def s_only_this_tab_no_confirm(ctx: Run, page):
    """对照：没有别的标签页快照时，“保留我的”照常一次完成（不多弹确认）。"""
    doc_id = ctx.doc(page, "方案C只本标签")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    assert not page.get_by_test_id("others-confirm").count()
    assert not ctx.items(doc_id)
    return {}


@scenario("M1 另一标签快照后 使用对方的版本 一次生效")
def s_other_tab_use_theirs(ctx: Run, page):
    """审查员 A3b：另一个标签页写了快照后，“使用对方的版本”不被卡住。"""
    doc_id = ctx.doc(page, "方案C另一标签用对方")
    ctx.make_conflict(page, doc_id, "manual", 300)
    hit = ctx.wait_snapshot(doc_id, MARK)
    ctx.li.call("POST", f"/documents/{doc_id}/conflicts/{hit['conflictId']}/mine", json={
        "itemIndex": hit["itemIndex"], "markdown": P3 + "〔另一标签写的〕", "clientSaveKey": str(uuid.uuid4()), "tabId": "other-tab", "seq": int(time.time() * 1000)})
    open_panel(page)
    card(page).get_by_role("button", name="使用对方的版本").click()
    card(page).locator(".ccard-confirm").get_by_role("button", name="使用对方的版本").click()
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    assert not ctx.items(doc_id), "冲突仍未处理"
    assert P3_THEIRS in ctx.server(doc_id)["markdownContent"].split("\n\n")
    return {}


@scenario("K1-M1 重开后正文里改冲突段 保留我的 不算到对方")
def s_reopen_self_edit(ctx: Run, page):
    """测试工程师 K1-M1（设计师定稿）：刷新后在正文第 3 段（对方版本）续写，再点“保留我的”：
    提示引用补写的字；标题“正文当前版本（含 张工 的修改和你刚补的内容）”；差异里补字单独标出；
    按钮临时改为“仍然保留我的（覆盖刚补的内容）”。"""
    reopen_self_edit_setup(ctx, page, "方案C重开后改正文")
    card(page).get_by_role("button", name="保留我的").click()
    notice = card(page).locator(".ccard-notice")
    expect(notice).to_contain_text("你重新打开后在正文里又改了这一段（“〔后补XY〕”）", timeout=20000)
    expect(notice).to_contain_text("你刚补的这些字会被覆盖")
    expect(card(page).locator(".theirs h4")).to_contain_text("正文当前版本（含 张工 的修改和你刚补的内容）· 已在正文中")
    expect(card(page).get_by_test_id("theirs-self-add")).to_have_text("〔后补XY〕")
    ins = " ".join(card(page).locator(".theirs ins").all_inner_texts())
    assert "后补" not in ins, f"补的字被算成张工的：{ins}"
    expect(card(page).get_by_role("button", name="仍然保留我的（覆盖刚补的内容）")).to_be_visible()
    assert "张工 又修改了一次" not in page.locator("body").inner_text()
    assert_no_self_named(page)
    return {"notice": notice.inner_text()[:80]}


def reopen_self_edit_setup(ctx: Run, page, name):
    doc_id = ctx.doc(page, name)
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    page.reload()
    expect(page.locator(".paper-content")).to_be_visible(timeout=90000)
    base.wait_saved(page, 90000)
    base.type_after(page, "第3段", "〔后补XY〕")
    deadline = time.time() + 20
    while "〔后补XY〕" not in ctx.server(doc_id)["markdownContent"] and time.time() < deadline:
        time.sleep(0.5)
    assert "〔后补XY〕" in ctx.server(doc_id)["markdownContent"], "正文自动保存没进来"
    open_panel(page)
    return doc_id


def assert_kept_both(ctx: Run, page, doc_id):
    deadline = time.time() + 20
    md = ""
    while time.time() < deadline:
        md = ctx.server(doc_id)["markdownContent"]
        if not ctx.items(doc_id) and "〔后补XY〕" in md and MARK in md:
            break
        time.sleep(0.5)
    assert "〔后补XY〕" in md and MARK in md and md.count(MARK) == 1 and md.count("〔后补XY〕") == 1, md[:500]
    body = page.locator(".paper-content").inner_text()
    deadline = time.time() + 15
    while not ("〔后补XY〕" in body and MARK in body) and time.time() < deadline:
        page.wait_for_timeout(500)
        body = page.locator(".paper-content").inner_text()
    assert "〔后补XY〕" in body and MARK in body, body[:400]


@scenario("K1-M1 重开补字后 两段都保留 不丢字")
def s_reopen_self_edit_both(ctx: Run, page):
    doc_id = reopen_self_edit_setup(ctx, page, "方案C重开补字两段都保留")
    # 服务端“两段都保留”是把我的版本插在正文当前这一段（含刚补的字）之后，不需要再确认；若提示了就再点一次
    card(page).get_by_role("button", name="两段都保留").click()
    page.wait_for_timeout(1500)
    notice = page.locator(".conflict-panel .ccard-notice")
    if notice.count():
        expect(notice.first).to_contain_text("再点「两段都保留」")
        card(page).get_by_role("button", name="两段都保留").click()
    assert_kept_both(ctx, page, doc_id)
    assert_no_self_named(page)
    return {}


@scenario("K1-M1 重开补字后 手动合并 不丢字")
def s_reopen_self_edit_manual(ctx: Run, page):
    doc_id = reopen_self_edit_setup(ctx, page, "方案C重开补字手动合并")
    card(page).get_by_role("button", name="手动合并").click()
    area = card(page).locator("textarea")
    card(page).get_by_role("button", name="插入我的版本").click()
    assert MARK in area.input_value()
    card(page).get_by_role("button", name="采用合并结果").click()
    notice = card(page).locator(".ccard-notice")
    if notice.count() or "〔后补XY〕" not in area.input_value():
        expect(notice).to_contain_text("合并框里的正文版本已换成当前版本", timeout=20000)
        value = area.input_value()
        assert "〔后补XY〕" in value and MARK in value, value
        card(page).get_by_role("button", name="采用合并结果").click()
    assert_kept_both(ctx, page, doc_id)
    assert_no_self_named(page)
    return {}


@scenario("M2 隐形框存不上 先记草稿 联网补发 关页重开补发")
def s_sink_offline(ctx: Run, page):
    """审查员 M2（p9-b2b）：/mine 失败时隐形框里的字先进本机草稿；online 后补发；关页后重开从草稿补发；框可见。"""
    selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
    doc_id = selfconf.make_doc(ctx.data, f"方案C隐形框断网-{time.strftime('%H%M%S')}", 0)
    ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + LIST)
    ctx.open(page, doc_id)
    page.locator(".paper-content li", has_text="列表项一").first.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    ctx.zhang_edit(doc_id, "列表项一", "列表项一（对方改）")
    page.keyboard.press("Control+s")
    page.keyboard.type("，续零】", delay=150)
    sink = page.get_by_test_id("caret-sink")
    expect(sink).to_be_visible(timeout=20000)
    box = sink.bounding_box() or {}
    style = sink.evaluate("el => getComputedStyle(el).opacity")
    assert box.get("width", 0) > 100 and style == "1", f"隐形框不可见：{box} opacity={style}"
    page.wait_for_timeout(1500)
    page.route("**/conflicts/*/mine", lambda route: route.abort())
    page.keyboard.type("〔存不上〕", delay=60)
    page.wait_for_timeout(2500)
    latest = lambda: "\n".join((item.get("mineLatest") or {}).get("markdown") or "" for item in ctx.items(doc_id))  # noqa: E731
    assert "〔存不上〕" not in latest()
    drafts = page.evaluate("() => Object.keys(localStorage).map(k => localStorage.getItem(k) || '').join('\\n')")
    assert "〔存不上〕" in drafts, "存不上时没先记进本机草稿"
    expect(page.get_by_test_id("sink-note")).to_contain_text("暂时没存上", timeout=5000)
    page.unroute("**/conflicts/*/mine")
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    deadline = time.time() + 15
    while "〔存不上〕" not in latest() and time.time() < deadline:
        time.sleep(0.5)
    assert "〔存不上〕" in latest(), "联网后没补发"
    # 关页前存不上：重开后从本机草稿补发
    page.route("**/conflicts/*/mine", lambda route: route.abort())
    page.keyboard.type("〔关页前〕", delay=60)
    page.wait_for_timeout(1500)
    url = page.url
    other = page.context.new_page()
    page.close(run_before_unload=False)
    try:
        other.goto(url)
        expect(other.locator(".paper-content")).to_be_visible(timeout=90000)
        deadline = time.time() + 40
        while "〔关页前〕" not in latest() and time.time() < deadline:
            time.sleep(0.5)
        got = latest()
        assert "〔关页前〕" in got and "〔存不上〕" in got and "【我改的" in got, got
        assert "〔关页前〕" not in ctx.server(doc_id)["markdownContent"]
    finally:
        other.context.close()
    return {"latest": got[-60:]}


@scenario("K6-L 刷新后再关页重开 无幽灵草稿条")
def s_reload_close_reopen(ctx: Run, page):
    """测试工程师 K6-L：续写存不上时刷新（从草稿补发成快照），再关页重开，都不能出现恢复条或锁定编辑。"""
    doc_id = ctx.doc(page, "方案C刷新关页")
    ctx.make_conflict(page, doc_id, "manual", 300)
    ctx.wait_snapshot(doc_id, MARK)
    page.route("**/conflicts/*/mine", lambda route: route.abort())
    base.type_after(page, "第3段", "〔刷新前续写〕")
    page.wait_for_timeout(4500)
    page.unroute("**/conflicts/*/mine")
    page.reload()
    expect(page.locator(".paper-content")).to_be_visible(timeout=90000)
    hit = ctx.wait_snapshot(doc_id, "〔刷新前续写〕", 30)
    assert "〔刷新前续写〕" in ((hit.get("mineLatest") or {}).get("markdown") or ""), hit
    page.wait_for_timeout(3000)
    rows = {"reload_bar": page.get_by_test_id("draft-restore").count()}
    assert page.locator(".paper-content").get_attribute("contenteditable") == "true", "刷新后编辑区被锁"
    url = page.url
    other = page.context.new_page()
    page.close(run_before_unload=False)
    other.goto(url)
    expect(other.locator(".paper-content")).to_be_visible(timeout=90000)
    other.wait_for_timeout(3000)
    rows["reopen_bar"] = other.get_by_test_id("draft-restore").count()
    editable = other.locator(".paper-content").get_attribute("contenteditable")
    other.context.close()
    assert not rows["reload_bar"] and not rows["reopen_bar"], f"出现了幽灵草稿条 {rows}"
    assert editable == "true", "重开后编辑区被锁"
    return rows


def list_conflict_with_sink(ctx: Run, page, name: str, filler: int = 0) -> str:
    """列表项冲突：李工改列表项一、张工也改，409 后这一项换成对方版本、焦点进冲突输入框。"""
    selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
    doc_id = selfconf.make_doc(ctx.data, f"{name}-{time.strftime('%H%M%S')}", filler)
    ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + LIST)
    ctx.open(page, doc_id)
    page.locator(".paper-content li", has_text="列表项一").first.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    ctx.zhang_edit(doc_id, "列表项一", "列表项一（对方改）")
    page.keyboard.press("Control+s")
    page.keyboard.type("，续零】", delay=150)
    expect(page.get_by_test_id("caret-sink")).to_be_visible(timeout=20000)
    expect(page.locator(".paper-content li", has_text="列表项一（对方改）")).to_be_visible(timeout=20000)
    page.wait_for_timeout(800)
    return doc_id


@scenario("A 冲突输入框贴着该项 随正文滚动 按内容增高")
def s_sink_position(ctx: Run, page):
    """设计师必改 A：输入框在被替换的那一项正下方（不挡住它），随正文滚动，长句自动增高（≤4 行），占位文字不截断。"""
    list_conflict_with_sink(ctx, page, "方案C输入框位置", 40)
    sink = page.get_by_test_id("caret-sink")
    item = page.locator(".paper-content li", has_text="列表项一（对方改）").first
    s0, i0 = sink.bounding_box(), item.bounding_box()
    gap = s0["y"] - (i0["y"] + i0["height"])
    assert -1 <= gap <= 24, f"输入框不在该项正下方：项 {i0} 框 {s0}"
    assert abs(s0["x"] - i0["x"]) <= 60, f"输入框没和该项左对齐：项 {i0} 框 {s0}"
    fits = sink.evaluate("el => el.scrollHeight <= el.clientHeight + 2")
    assert fits, "占位文字被截断"
    page.locator(".desk").evaluate("el => el.scrollBy(0, 240)")
    page.wait_for_timeout(300)
    s1, i1 = sink.bounding_box(), item.bounding_box()
    assert abs((s1["y"] - s0["y"]) - (i1["y"] - i0["y"])) <= 2 and abs(i1["y"] - i0["y"]) > 100, f"输入框没跟随滚动：{s0}->{s1} 项 {i0}->{i1}"
    page.locator(".desk").evaluate("el => el.scrollBy(0, -240)")
    h1 = sink.bounding_box()["height"]
    sink.focus()
    page.keyboard.type("这是一句很长的续写内容，用来检查输入框会不会按内容自动增高，而不是只显示最后一行。" * 2, delay=5)
    page.wait_for_timeout(300)
    h2 = sink.bounding_box()["height"]
    assert h2 > h1 + 15, f"长句时输入框没有增高：{h1}->{h2}"
    assert h2 <= 4 * 20 + 16, f"输入框超过 4 行：{h2}"
    border = sink.evaluate("el => getComputedStyle(el).borderTopStyle + ' ' + getComputedStyle(el).borderTopColor")
    assert border.startswith("solid") and "150, 90, 0" in border, f"边框不是 token 实线：{border}"
    # 设计师 S1/S3：说明在重绘后写“已换成”；浮层在格式工具栏下方；窄宽度下“知道了”不折行
    note = page.get_by_test_id("sink-note")
    expect(note).to_contain_text("已换成 张工 的版本", timeout=10000)
    tool, nb = page.locator(".etool").bounding_box(), note.bounding_box()
    assert nb["y"] >= tool["y"] + tool["height"] - 1, f"说明盖住了工具栏：工具栏 {tool} 说明 {nb}"
    page.set_viewport_size({"width": 1024, "height": 720})
    page.wait_for_timeout(300)
    ok_btn = note.get_by_role("button", name="知道了")
    lh = ok_btn.evaluate("el => parseFloat(getComputedStyle(el).lineHeight) || 20")
    assert ok_btn.bounding_box()["height"] <= lh * 1.5, f"“知道了”折行：{ok_btn.bounding_box()}"
    return {"gap": round(gap, 1), "h": [round(h1), round(h2)]}


def resolve_as_other_tab(ctx: Run, doc_id: str, hit: dict, action: str) -> None:
    """同一账号的另一个标签页处理这条冲突（与界面上点按钮走同一接口）。"""
    rev = ctx.server(doc_id)["revision"]
    item = {"itemIndex": hit["itemIndex"], "action": action}
    if action == "keep_mine":
        item["theirsRevision"] = (hit.get("theirs") or {}).get("revision")
    ctx.li.call("POST", f"/documents/{doc_id}/conflicts/{hit['conflictId']}/resolve",
                json={"clientSaveKey": str(uuid.uuid4()), "baseDocumentRevision": rev, "items": [item]})


def wait_body(ctx: Run, doc_id: str, text: str, timeout: float = 30) -> str:
    deadline = time.time() + timeout
    while True:
        md = ctx.server(doc_id)["markdownContent"]
        if text in md or time.time() > deadline:
            return md
        time.sleep(0.5)


def other_tab_resolved_round(ctx: Run, page, action: str) -> dict:
    doc_id = ctx.doc(page, f"方案C另一标签已处理{action}")
    ctx.make_conflict(page, doc_id, "manual", 300)
    page.keyboard.type("〔甲批〕", delay=40)
    hit = ctx.wait_snapshot(doc_id, "〔甲批〕")
    assert "〔甲批〕" in hit["mineLatest"]["markdown"], hit
    resolve_as_other_tab(ctx, doc_id, hit, action)
    page.keyboard.type("〔乙批，丙批〕", delay=60)
    note = page.get_by_test_id("sink-note")
    expect(note).to_contain_text("已在你另一个标签页处理", timeout=30000)
    expect(note).to_contain_text("已接到这一段末尾", timeout=30000)
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=20000)
    md = wait_body(ctx, doc_id, "〔乙批，丙批〕")
    block = next((blk for blk in md.split("\n\n") if "〔乙批，丙批〕" in blk), "")
    head = (P3 + MARK) if action == "keep_mine" else P3_THEIRS
    assert block.startswith(head) and block.endswith("〔乙批，丙批〕"), f"多写的字没接回这一段末尾：{md[:400]}"
    assert md.count("〔乙批，丙批〕") == 1, md[:400]
    assert "其他成员 更新了你正在看的这一段" not in page.locator("body").inner_text()
    base.wait_saved(page, 30000)
    page.reload()
    expect(page.locator(".paper-content")).to_contain_text("〔乙批，丙批〕", timeout=90000)
    return {"block": block[-40:]}


@scenario("B 另一标签已处理 冻结段续写不丢 保留我的")
def s_other_tab_resolved_keep(ctx: Run, page):
    return other_tab_resolved_round(ctx, page, "keep_mine")


@scenario("B 另一标签已处理 冻结段续写不丢 使用对方的")
def s_other_tab_resolved_theirs(ctx: Run, page):
    return other_tab_resolved_round(ctx, page, "use_theirs")


@scenario("B 另一标签已处理 隐形框续写不丢")
def s_other_tab_resolved_sink(ctx: Run, page):
    doc_id = list_conflict_with_sink(ctx, page, "方案C另一标签已处理隐形框")
    hit = ctx.wait_snapshot(doc_id, "续零")
    resolve_as_other_tab(ctx, doc_id, hit, "use_theirs")
    page.get_by_test_id("caret-sink").focus()
    page.keyboard.type("〔隐形框后写〕", delay=60)
    expect(page.get_by_test_id("sink-note")).to_contain_text("已接到这一段末尾", timeout=30000)
    md = wait_body(ctx, doc_id, "〔隐形框后写〕")
    line = next((ln for ln in md.split("\n") if "〔隐形框后写〕" in ln), "")
    assert "列表项一（对方改）" in line, f"没接回这一项末尾：{md[:400]}"
    return {"line": line[-40:]}


SCENARIOS = [s_repro, s_dom, s_other, s_keep_mine, s_use_theirs, s_keep_both, s_manual, s_leave, s_reload_draft, s_reconflict, s_copy, s_copy_export_ai, s_tm2,
             s_timing_small, s_timing_big, s_tablist, s_load_lock, s_self_theirs, s_list_batches, s_offline_reopen,
             s_other_tab_keep_mine, s_other_tab_use_theirs, s_other_tab_then_mine, s_only_this_tab_no_confirm, s_reopen_self_edit, s_reopen_self_edit_both, s_reopen_self_edit_manual, s_sink_offline, s_reload_close_reopen, s_sink_position,
             s_other_tab_resolved_keep, s_other_tab_resolved_theirs, s_other_tab_resolved_sink]


def snapshot_bytes(conflict_id: str) -> int:
    from app.db import connect
    with connect() as connection:
        row = connection.execute("SELECT COALESCE(SUM(LENGTH(CAST(markdown AS BLOB))), 0) AS n FROM document_conflict_mine_snapshots "
                                 "WHERE conflict_id=%s", (conflict_id,)).fetchone()
    return int(row["n"])


def fill_snapshots(ctx: Run, doc_id: str, hit: dict, rest: int = 20) -> None:
    """把这条冲突的快照总大小推到只剩 rest 字节（比冲突段本身还小；不带标签页，不进 mineOthers）。"""
    from app.document_commit import MAX_SNAPSHOT_BYTES_PER_CONFLICT as cap
    for n in range(40):
        room = cap - snapshot_bytes(hit["conflictId"]) - rest
        if room <= 0:
            return
        size = min(room, 5_900_000)
        tail = f"〔填满{n:03d}〕"
        body = size - len(tail.encode())
        md = tail + "满" * max(0, body // 3) + "x" * max(0, body % 3) if body > 0 else "x" * size
        ctx.li.call("POST", f"/documents/{doc_id}/conflicts/{hit['conflictId']}/mine", json={
            "itemIndex": hit["itemIndex"], "markdown": md, "clientSaveKey": str(uuid.uuid4())})
    raise AssertionError("填不满快照")


def capped_conflict(ctx: Run, page, name: str) -> tuple[str, dict]:
    doc_id = ctx.doc(page, name)
    ctx.make_conflict(page, doc_id, "manual", 300)
    hit = ctx.wait_snapshot(doc_id, MARK)
    fill_snapshots(ctx, doc_id, hit)
    base.type_after(page, MARK, "〔满后写〕")
    page.wait_for_timeout(5000)
    return doc_id, hit


@scenario("N3 副本满 处理不再要求先保存 保留我的用编辑区最新一版")
def s_capped_keep_mine(ctx: Run, page):
    """测试工程师 N3（用户决定）：快照到 20MB 上限后，处理按钮不再被“请先保存”挡住；处理请求带上编辑区里没存上的最新“我的版本”。"""
    doc_id, hit = capped_conflict(ctx, page, "方案C副本满保留")
    assert "〔满后写〕" not in ((ctx.wait_snapshot(doc_id, "〔满后写〕", 2).get("mineLatest") or {}).get("markdown") or ""), "前提不成立：没到上限"
    open_panel(page)
    status = card(page).get_by_test_id("conflict-mine-status")
    expect(status).to_contain_text("副本已满", timeout=15000)
    assert "联网后自动保存" not in status.inner_text()
    card(page).get_by_role("button", name="保留我的").click()
    # 面板是冲突时自动打开的（填满之前）：服务器上那份“填满”的是另一处写的、本标签没有 → 与 A3c 同一个确认（N4 并入）
    confirm = card(page).get_by_test_id("others-confirm")
    confirmed = False
    try:
        expect(confirm).to_be_visible(timeout=8000)
        expect(confirm).to_contain_text("〔满后写〕")
        confirm.get_by_role("button", name="只用这个标签页的版本").click()
        confirmed = True
    except AssertionError:
        pass
    expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=30000)
    assert not page.locator(".conflict-panel .ccard-error").filter(has_text="请先保存").count()
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    assert MARK + "〔满后写〕" in md, md[:400]
    return {"confirmed": confirmed}


@scenario("N3 副本满 刷新后恢复草稿 新写的字不丢")
def s_capped_reload_restore(ctx: Run, page):
    """测试工程师 N3：副本满后刷新 →（恢复并保存）→ 冲突卡“我的版本”仍是草稿里的最新字，保留我的后进正文。"""
    doc_id, hit = capped_conflict(ctx, page, "方案C副本满刷新")
    page.wait_for_function("t => Object.values(localStorage).some(v => v.includes(t))", arg="〔满后写〕", timeout=15000)
    # 正文另一段也有没存上的修改（保存请求被拦下），刷新后才有“恢复并保存”（QA snap20_ui3 ACT=reload_restore 的路径）
    # 只拦正文保存（PATCH），刷新时页面关闭前的 keepalive 保存也拦下；刷新后再放开
    block = "**/documents/*/content"
    page.route(block, lambda route: route.abort() if route.request.method == "PATCH" else route.continue_())
    base.type_after(page, "第5段", "〔正文改〕")
    page.wait_for_function("t => Object.values(localStorage).some(v => v.includes(t))", arg="〔正文改〕", timeout=15000)
    page.wait_for_timeout(1500)
    page.reload()
    expect(page.locator(".paper-content")).to_be_visible(timeout=90000)
    page.unroute(block)
    page.wait_for_timeout(5000)
    bar = page.get_by_test_id("draft-recovery")
    restored = False
    if bar.count() and bar.get_by_role("button", name="恢复并保存").count():
        bar.get_by_role("button", name="恢复并保存").click()
        restored = True
        page.wait_for_timeout(6000)
    open_panel(page)
    expect(card(page)).to_contain_text("〔满后写〕", timeout=20000)
    expect(card(page).get_by_test_id("conflict-mine-status")).to_contain_text("副本已满")
    card(page).get_by_role("button", name="保留我的").click()
    deadline = time.time() + 30
    while ctx.items(doc_id) and time.time() < deadline:
        time.sleep(0.5)
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    assert "〔满后写〕" in md, md[:400]
    # 关页时的 keepalive 保存不一定被 route 拦下（落库了就没有恢复条）；两种路径下两处的字都不能丢
    assert "〔正文改〕" in md, ("正文另一段的修改丢了", restored, md[:400])
    expect(page.locator(".paper-content")).to_contain_text("〔满后写〕", timeout=15000)
    return {"restored": restored}


@scenario("N8 同一人两个标签同段都冲突 后保存的标签冻结不丢字 按标签各存一份")
def s_two_tabs_same_paragraph(ctx: Run, page):
    """测试工程师 N8（mtab_t6f）：两个标签页都改了第 3 段、都遇到冲突。后保存的标签返回 200 + frozenByConflict，
    编辑区这一段要冻结（不被重绘掉），两边续写按标签各存一份；处理时卡片列出另一标签的（mineOthers），手动合并两份都在。"""
    doc_id = ctx.doc(page, "方案C两标签同段")
    other = page.context.new_page()
    base.open_pages.append(other)
    ctx.open(other, doc_id)
    base.type_after(page, "第3段", "【甲写】")
    base.type_after(other, "第3段", "【乙写】")
    ctx.zhang_edit(doc_id, P3, P3_THEIRS)
    other.bring_to_front()
    other.keyboard.press("Control+s")
    expect(other.locator(".paper-content [data-frozen]").first).to_be_visible(timeout=30000)
    page.bring_to_front()
    page.keyboard.press("Control+s")
    expect(page.locator(".paper-content [data-frozen]").first).to_be_visible(timeout=30000)
    expect(page.locator(".paper-content [data-frozen]").first).to_contain_text("【甲写】")
    page.keyboard.type("〔甲续〕", delay=40)
    ctx.wait_snapshot(doc_id, "〔甲续〕")
    other.bring_to_front()
    other.keyboard.type("〔乙续〕", delay=40)
    hit = ctx.wait_snapshot(doc_id, "〔乙续〕")
    assert any("〔甲续〕" in tab["markdown"] for tab in hit.get("mineOthers") or []), hit.get("mineOthers")
    md = ctx.server(doc_id)["markdownContent"]
    assert "【甲写】" not in md and "【乙写】" not in md, "冲突段不应写进正文"
    page.bring_to_front()
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    confirm = card(page).get_by_test_id("others-confirm")
    expect(confirm).to_be_visible(timeout=20000)
    expect(confirm.get_by_test_id("others-text")).to_contain_text("〔乙续〕")
    confirm.get_by_role("button", name="手动合并（两份都放进去）").click()
    card(page).get_by_role("button", name="采用合并结果").click()
    deadline = time.time() + 20
    while ctx.items(doc_id) and time.time() < deadline:
        time.sleep(0.5)
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    for text in ("【甲写】", "〔甲续〕", "【乙写】", "〔乙续〕"):
        assert text in md, (text, md[:500])
    return {}


def fill_snapshot_count(conflict_id: str, item_index: int) -> int:
    """设计师复验 2 D：按库内复制最新一份把份数补到上限（内容与本标签存过的那份相同）。"""
    from app.db import connect
    from app.document_commit import MAX_SNAPSHOTS_PER_ITEM as cap
    with connect() as connection:
        row = connection.execute("SELECT * FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND item_index=%s ORDER BY rowid DESC LIMIT 1",
                                 (conflict_id, item_index)).fetchone()
        have = connection.execute("SELECT COUNT(*) AS n FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND item_index=%s",
                                  (conflict_id, item_index)).fetchone()["n"]
        for _ in range(cap - int(have)):
            connection.execute("INSERT INTO document_conflict_mine_snapshots(id, conflict_id, item_index, actor_id, markdown, client_save_key, tab_id, source) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s,'editor')",
                               (str(uuid.uuid4()), conflict_id, item_index, row["actor_id"], row["markdown"], f"fill-{uuid.uuid4()}", row["tab_id"]))
        connection.commit()
    return cap


@scenario("D 本标签存过一份后副本满 不刷新 保留我的")
def s_capped_count_no_reload(ctx: Run, page):
    """设计师复验 2 D：本标签先存成功一份，再到 500 份上限，不刷新直接点「保留我的」——不能 409 后无反应。"""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)[:300]))
    page.on("console", lambda message: errors.append(f"{message.type}: {message.text[:300]}") if message.type in ("error", "warning") else None)
    doc_id = ctx.doc(page, "方案C副本满份数")
    ctx.make_conflict(page, doc_id, "manual", 300)
    hit = ctx.wait_snapshot(doc_id, MARK)
    fill_snapshot_count(hit["conflictId"], hit["itemIndex"])
    base.type_after(page, MARK, "〔满后写〕")
    page.wait_for_timeout(5000)
    open_panel(page)
    expect(card(page).get_by_test_id("conflict-mine-status")).to_contain_text("副本已满", timeout=15000)
    card(page).get_by_role("button", name="保留我的").click()
    confirm = card(page).get_by_test_id("others-confirm")
    try:
        expect(confirm).to_be_visible(timeout=5000)
        confirm.get_by_role("button", name="只用这个标签页的版本").click()
    except AssertionError:
        pass
    try:
        expect(page.locator(".paper-content [data-frozen]")).to_have_count(0, timeout=30000)
    except AssertionError:
        raise AssertionError(f"冻结区仍在；页面错误：{errors[-6:]}；卡片：{card(page).inner_text()[-300:]}".replace("\n", " ")) from None
    assert not ctx.items(doc_id), "冲突仍未处理"
    md = ctx.server(doc_id)["markdownContent"]
    assert MARK + "〔满后写〕" in md, md[:400]
    return {}


@scenario("K 重开补字后 合并框改过 不自动替换 提交前提醒 插入补字")
def s_reopen_self_edit_manual_edited(ctx: Run, page):
    """设计师复验 2 K：用户改过合并框后再次冲突，不能说“已换成当前版本”、不能静默覆盖补写的字。"""
    doc_id = reopen_self_edit_setup(ctx, page, "方案C重开补字合并框改过")
    card(page).get_by_role("button", name="手动合并").click()
    area = card(page).locator("textarea")
    area.fill("第3段：我自己改写的合并结果〔改写〕")
    card(page).get_by_role("button", name="采用合并结果").click()
    notice = card(page).locator(".ccard-notice")
    expect(notice).to_contain_text("合并框你已改过，没有自动替换", timeout=20000)
    assert area.input_value() == "第3段：我自己改写的合并结果〔改写〕", area.input_value()
    card(page).get_by_role("button", name="采用合并结果").click()
    expect(notice).to_contain_text("合并框里没有你刚在正文里补的", timeout=10000)
    assert ctx.items(doc_id), "提醒前就提交了"
    card(page).get_by_test_id("insert-self-added").click()
    assert "〔后补XY〕" in area.input_value()
    card(page).get_by_role("button", name="采用合并结果").click()
    deadline = time.time() + 20
    while ctx.items(doc_id) and time.time() < deadline:
        time.sleep(0.5)
    md = ctx.server(doc_id)["markdownContent"]
    assert not ctx.items(doc_id) and "〔改写〕" in md and "〔后补XY〕" in md, md[:400]
    return {}


SCENARIOS += [s_reopen_self_edit_manual_edited, s_capped_count_no_reload, s_capped_keep_mine, s_capped_reload_restore, s_two_tabs_same_paragraph]


def main() -> int:
    base.require_targets()
    data = base.seed()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        ctx = Run(data, browser)
        for fn in SCENARIOS:
            fn(ctx)
        browser.close()
    bases = check_bases(ctx)
    ok7 = not bases["self_base"]
    rows.append({"name": "7 冲突 base 不是本人提交过的文字", "ok": ok7, **bases})
    print(("PASS" if ok7 else "FAIL") + f" 7 冲突 base 不是本人提交过的文字 {bases}")
    passed = sum(1 for row in rows if row["ok"])
    print(f"\ne2e_scheme_c {passed}/{len(rows)} passed")
    return 0 if passed == len(rows) else 1


def sink_gap(page, item) -> float:
    s, i = page.get_by_test_id("caret-sink").bounding_box(), item.bounding_box()
    return s["y"] - (i["y"] + i["height"])


@scenario("A1 同名条目 输入框贴在冲突所在列表的那一项下")
def s_sink_duplicate_name(ctx: Run, page):
    """设计师复验 2 A1：前文另一个列表里已有同名的“列表项一（对方改）”，输入框仍须贴在冲突那一项下面，而不是全文第一个同名项。"""
    selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
    doc_id = selfconf.make_doc(ctx.data, f"方案C同名条目-{time.strftime('%H%M%S')}", 0)
    ctx.zhang_edit(doc_id, "第2段：原始内容 2。", "第2段：原始内容 2。\n\n- 列表项一（对方改）\n- 另一组的项")
    ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + LIST)
    ctx.open(page, doc_id)
    page.locator(".paper-content li").filter(has_text=re.compile(r"^列表项一$")).first.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    ctx.zhang_edit(doc_id, "- 列表项一\n- 列表项二", "- 列表项一（对方改）\n- 列表项二")
    page.keyboard.press("Control+s")
    page.keyboard.type("，续零】", delay=150)
    expect(page.get_by_test_id("caret-sink")).to_be_visible(timeout=20000)
    target = page.locator(".paper-content").locator("xpath=.//li[following-sibling::li[1][normalize-space()='列表项二']]")
    expect(target).to_have_count(1, timeout=20000)
    expect(target).to_contain_text("列表项一（对方改）", timeout=20000)
    page.wait_for_timeout(800)
    decoy = page.locator(".paper-content").locator("xpath=.//li[following-sibling::li[1][normalize-space()='另一组的项']]")
    gap = sink_gap(page, target)
    assert -1 <= gap <= 24, f"输入框没贴在冲突那一项下：gap={gap} 同名项 gap={sink_gap(page, decoy)}"
    return {"gap": round(gap, 1)}


@scenario("A2 条目在视口底部 输入框滚入可视区")
def s_sink_bottom_visible(ctx: Run, page):
    """设计师复验 2 A2：冲突项在可视区最底部时，输入框（含增高后）要完整可见。"""
    selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 41)]
    doc_id = selfconf.make_doc(ctx.data, f"方案C底部输入框-{time.strftime('%H%M%S')}", 30)
    ctx.zhang_edit(doc_id, "第38段：原始内容 38。", "第38段：原始内容 38。\n\n" + LIST)
    ctx.open(page, doc_id)
    item = page.locator(".paper-content li", has_text="列表项一").first
    item.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    page.locator(".desk").evaluate("""(desk) => {
        const li = [...desk.querySelectorAll('.paper-content li')].find(el => el.textContent.startsWith('列表项一'));
        desk.scrollTop += li.getBoundingClientRect().bottom - desk.getBoundingClientRect().bottom + 4;
    }""")
    page.wait_for_timeout(300)
    lift = item.evaluate("el => el.closest('.desk').getBoundingClientRect().bottom - el.getBoundingClientRect().bottom")
    assert -2 <= lift <= 30, f"前置条件：列表项没有停在可视区底部（距底 {lift}）"
    ctx.zhang_edit(doc_id, "列表项一", "列表项一（对方改）")
    page.keyboard.press("Control+s")
    page.keyboard.type("，续零】", delay=150)
    sink = page.get_by_test_id("caret-sink")
    expect(sink).to_be_visible(timeout=20000)
    page.wait_for_timeout(800)
    inside = "el => { const d = el.closest('.desk').getBoundingClientRect(), b = el.getBoundingClientRect(); return [b.top - d.top, d.bottom - b.bottom]; }"
    top, room = sink.evaluate(inside)
    assert top >= 0 and room >= 0, f"输入框不在可视区：上 {top} 下余 {room}"
    sink.focus()
    page.keyboard.type("这是一句很长的续写内容，用来检查输入框增高后仍然完整可见。" * 3, delay=5)
    page.wait_for_timeout(500)
    top2, room2 = sink.evaluate(inside)
    assert top2 >= 0 and room2 >= 0, f"增高后输入框超出可视区：上 {top2} 下余 {room2}"
    return {"room": [round(room), round(room2)]}


SCENARIOS += [s_sink_duplicate_name, s_sink_bottom_visible]


DUP_LIST = "- 接收模块\n- 处理模块\n- 显示模块"
# 嵌套列表要用 Word 原生的多级列表（List Bullet 2）；markdown 写的缩进子项经空白 Word 往返后会被拍平
NESTED_LIST = [("接收模块", 1), ("处理模块", 1), ("子项甲", 2), ("子项乙", 2), ("显示模块", 1)]


def word_list_doc(ctx: Run, name: str, items: list[tuple[str, int]]) -> str:
    from io import BytesIO

    from docx import Document as WordDocument
    word = WordDocument()
    word.add_heading(f"{name}-{time.strftime('%H%M%S')}", 1)
    for n in range(1, 5):
        word.add_paragraph(f"第{n}段：原始内容 {n}。")
    for text, level in items:
        word.add_paragraph(text, style="List Bullet" if level == 1 else f"List Bullet {level}")
    for n in range(5, 9):
        word.add_paragraph(f"第{n}段：原始内容 {n}。")
    buffer = BytesIO()
    word.save(buffer)
    zhang = ctx.data["zhang"]
    upload = zhang.client.post("/files", files={"file": (f"{name}.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"purpose": "attachment", "projectId": ctx.data["project"]["id"]},
                               headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"schemec-{uuid.uuid4()}"})
    assert upload.status_code == 201, upload.text
    return open_mode(zhang, zhang.call("POST", f"/projects/{ctx.data['project']['id']}/documents",
                      json={"name": name, "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]}))["id"]


def sink_list_conflict(ctx: Run, page, name: str, markdown, click: str, old: str, new: str, trigger: str) -> str:
    """列表项冲突：李工在 click 那一项末尾打字 → 张工把 old 改成 new → 李工保存（Ctrl+S / 自动保存）→ 接着打字进冲突输入框。"""
    if isinstance(markdown, list):
        doc_id = word_list_doc(ctx, name, markdown)
    else:
        selfconf.PARAS = [f"第{n}段：原始内容 {n}。" for n in range(1, 9)]
        doc_id = selfconf.make_doc(ctx.data, f"{name}-{time.strftime('%H%M%S')}", 0)
        ctx.zhang_edit(doc_id, "第4段：原始内容 4。", "第4段：原始内容 4。\n\n" + markdown)
    ctx.open(page, doc_id)
    page.locator(".paper-content li").filter(has_text=re.compile(f"^{click}$")).first.click()
    page.keyboard.press("End")
    page.keyboard.type("【我改的")
    ctx.zhang_edit(doc_id, old, new)
    if trigger == "manual":
        page.keyboard.press("Control+s")
    else:
        page.wait_for_function("window.__patches.length > 0 && window.__patches.some(p => p.status === 409)", timeout=30000)
    page.keyboard.type("，续写看位置】", delay=150)
    expect(page.get_by_test_id("caret-sink")).to_be_visible(timeout=20000)
    page.wait_for_timeout(800)
    return doc_id


def own_box(page, li) -> dict:
    """li 自身文字（不含子列表）的矩形"""
    return li.evaluate("""el => { const r = document.createRange(); r.setStart(el, 0);
        const sub = [...el.childNodes].find(n => n.nodeType === 1 && /^(UL|OL)$/.test(n.tagName)); if (sub) r.setEndBefore(sub); else r.setEnd(el, el.childNodes.length);
        const b = r.getBoundingClientRect(); return {x: b.left, y: b.top, width: b.width, height: b.height}; }""")


@scenario("A1 同一列表里同名条目 冲突在第 2 项 输入框贴在第 2 项下")
def s_sink_duplicate_same_list(ctx: Run, page):
    """设计师复验 3 A1：张工把第 2 项“处理模块”改成与第 1 项同名的“接收模块”。输入框要在第 2 项下面，不能挂在第 1 项下盖住第 2 项。
    Ctrl+S 与自动保存各一轮。"""
    out = []
    for trigger in ("manual", "auto"):
        if trigger != "manual":
            page.context.close()
            page = ctx.page()
        doc_id = sink_list_conflict(ctx, page, f"复验3同列表同名-{trigger}", DUP_LIST, "处理模块", "- 处理模块", "- 接收模块", trigger)
        items = page.locator(".paper-content li").filter(has_text=re.compile(r"^接收模块$"))
        expect(items).to_have_count(2, timeout=20000)
        first, second = items.nth(0).bounding_box(), items.nth(1).bounding_box()
        sink = page.get_by_test_id("caret-sink").bounding_box()
        gap = sink["y"] - (second["y"] + second["height"])
        covers = sink["y"] < second["y"] + second["height"] and sink["y"] + sink["height"] > second["y"]
        assert -1 <= gap <= 24 and not covers, f"{trigger}：输入框没贴在第 2 项下（gap={gap:.1f}，盖住第 2 项={covers}；第 1 项 {first} 第 2 项 {second} 框 {sink}）"
        typed = page.get_by_test_id("caret-sink").input_value()
        assert "续写看位置" in typed or any("续写看位置" in ((item.get("mineLatest") or {}).get("markdown") or "") for item in ctx.items(doc_id)), "续写的字不见了"
        out.append({"trigger": trigger, "gap": round(gap, 1)})
    base.shot(page, "A1-同列表同名-输入框")
    page.context.close()  # 第二轮自己开的页
    return {"rounds": out}


@scenario("S-A3 嵌套列表 子项冲突 输入框贴在子项下")
def s_sink_nested_child(ctx: Run, page):
    """设计师复验 S-A3：子项“子项甲”被替换时，输入框要贴在子项甲下、与它左对齐，而不是按父项“处理模块”落到子项乙之后。
    与设计师复验 2 的 A-nested 同形：父项是列表第 2 项，冲突条目是整个父项（含子列表）。"""
    sink_list_conflict(ctx, page, "复验3嵌套子项", NESTED_LIST, "子项甲", "子项甲", "子项甲（对方改）", "manual")
    child = page.locator(".paper-content li").filter(has_text=re.compile(r"^子项甲（对方改）$"))
    expect(child).to_have_count(1, timeout=20000)
    c = child.bounding_box()
    own = own_box(page, child)
    sink = page.get_by_test_id("caret-sink").bounding_box()
    gap = sink["y"] - (c["y"] + c["height"])
    assert -1 <= gap <= 24, f"输入框没贴在子项下：gap={gap:.1f} 子项 {c} 框 {sink}"
    assert abs(sink["x"] - own["x"]) <= 12, f"输入框没和子项左对齐：子项文字 {own} 框 {sink}"
    base.shot(page, "S-A3-嵌套子项-输入框")
    return {"gap": round(gap, 1), "dx": round(sink["x"] - own["x"], 1)}


SCENARIOS += [s_sink_duplicate_same_list, s_sink_nested_child]


@scenario("C 整篇 FORMAT 冲突 文案说正文保持当前版本 不写其他成员")
def s_format_whole_copy(ctx: Run, page):
    """设计师复验 2 C：整篇 FORMAT 冲突（合并后 Word 写不回）不说“这一项已换成 X 的版本”，没有修改人时不写“其他成员”。
    浏览器里造不出真实的合并单元格 FORMAT 冲突（服务器侧由 pytest test_merge_table_word 覆盖），这里把第一次保存的响应换成服务器同形的 409。"""
    doc_id = ctx.doc(page, "方案C整篇格式冲突")
    server = ctx.server(doc_id)
    note = "这次保存的内容合并后无法保持 Word 格式，正文保持当前版本；你这次的修改都在冲突副本里，可下载后手动处理。"
    fired = []

    def fake(route):
        if route.request.method != "PATCH" or fired:
            return route.continue_()
        fired.append(1)
        mine = (route.request.post_data_json or {}).get("markdownContent", "")
        item = {"conflictId": "00000000-0000-4000-8000-00000000c0de", "itemIndex": 0, "attempt": 1, "reason": "FORMAT", "opIndexes": [], "objectIds": [],
                "location": {"headingPath": [], "ordinal": None, "kind": "other", "label": None}, "base": {"markdown": server["markdownContent"]},
                "theirs": {"markdown": server["markdownContent"], "deleted": False, "revision": None, "updatedBy": None, "updatedAt": None},
                "mine": {"markdown": mine}, "theirsRevisions": {}, "afterObjectId": None}
        body = {"error": {"code": "OBJECT_CONFLICT", "message": "有 1 处内容已被他人修改，你的修改已保留，请选择处理方式。",
                          "details": {"conflictId": item["conflictId"], "conflicts": [item], "appliableMarkdown": server["markdownContent"],
                                      "appliableBaseRevision": server["revision"]}}, "requestId": "e2e"}
        route.fulfill(status=409, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    page.route("**/documents/*/content", fake)
    page.locator(".paper-content p", has_text="第3段").first.click()
    page.keyboard.press("End")
    page.keyboard.type("【格式冲突】", delay=30)
    expect(page.get_by_test_id("whole-conflict-note").or_(page.get_by_test_id("sink-note")).first).to_contain_text(note, timeout=30000)
    page.wait_for_timeout(1500)
    texts = page.locator("body").inner_text()
    page.unroute("**/documents/*/content", fake)
    assert "其他成员" not in texts, "整篇冲突文案里出现“其他成员”"
    assert "已换成" not in texts, "整篇冲突文案说“已换成”"
    return {"fired": len(fired)}


SCENARIOS += [s_format_whole_copy]


def _snapshot_rows(doc_id: str) -> list[dict]:
    from app.db import connect
    with connect() as connection:
        got = connection.execute(
            "SELECT s.tab_id, s.source, s.markdown FROM document_conflict_mine_snapshots s JOIN document_conflicts k ON k.id=s.conflict_id "
            "WHERE k.document_id=%s ORDER BY s.rowid", (doc_id,)).fetchall()
    return [dict(row) for row in got]


@scenario("N9 两标签同段 先409的标签不续写直接关掉 冲突前的字不丢")
def s_two_tabs_first_409_closes(ctx: Run, page):
    """测试工程师 N9：同一账号两个标签改第3段（甲【甲写】、乙【乙写】都未保存），张工改了第3段；乙先 Ctrl+S（409 冻结），甲再 Ctrl+S。
    乙不再打字直接关掉：【乙写】要按乙的标签存成一份快照（存好前在本机草稿里），面板在存好前不说“不会丢失”；甲处理“保留我的”时走 A3c 确认。"""
    doc_id = ctx.doc(page, "方案C两标签先409不续写")
    tab_b = page.context.new_page()
    base.open_pages.append(tab_b)
    ctx.open(tab_b, doc_id)
    base.type_after(page, "第3段", "【甲写】")
    base.type_after(tab_b, "第3段", "【乙写】")
    ctx.zhang_edit(doc_id, P3, P3_THEIRS)
    tab_b.bring_to_front()
    tab_b.keyboard.press("Control+s")
    expect(tab_b.locator(".paper-content [data-frozen]").first).to_be_visible(timeout=20000)
    in_draft = tab_b.evaluate("t => Object.values(localStorage).some(v => v.includes(t))", "【乙写】")
    page.bring_to_front()
    page.keyboard.press("Control+s")
    deadline = time.time() + 20
    while time.time() < deadline and not any("【乙写】" in (row["markdown"] or "") and row["tab_id"] for row in _snapshot_rows(doc_id)):
        time.sleep(0.5)
    rows = _snapshot_rows(doc_id)
    assert any("【乙写】" in (row["markdown"] or "") and row["tab_id"] for row in rows), f"乙的字没按标签存成快照：{rows}"
    tab_b.bring_to_front()
    open_panel(tab_b)
    expect(tab_b.get_by_test_id("conflict-panel-note")).to_contain_text("不会丢失", timeout=20000)
    tab_b.close()
    page.bring_to_front()
    page.wait_for_timeout(1500)
    open_panel(page)
    card(page).get_by_role("button", name="保留我的").click()
    confirm = card(page).get_by_test_id("others-confirm")
    expect(confirm).to_be_visible(timeout=20000)
    expect(confirm).to_contain_text("【乙写】")
    confirm.get_by_role("button", name="手动合并（两份都放进去）").click()
    value = card(page).locator("textarea").input_value()
    assert "【甲写】" in value and "【乙写】" in value, value
    assert value.count("第3段：原始内容 3。") == 1, f"公共开头在合并框里重复：{value}"
    card(page).get_by_role("button", name="采用合并结果").click()
    deadline = time.time() + 20
    while ctx.items(doc_id) and time.time() < deadline:
        time.sleep(0.5)
    md = ctx.server(doc_id)["markdownContent"]
    assert "【甲写】" in md and "【乙写】" in md, md[:400]
    return {"b_draft_before_snapshot": in_draft, "rows": len(rows)}


SCENARIOS += [s_two_tabs_first_409_closes]


# —— 设计师复验 3 C-2：合并单元格 422 →［撤销这一段的修改］→ 同一次保存里别处的修改照常保存 ——
def str_doc(ctx: Run, name: str) -> str:
    source = next((base.ROOT / "438C-2021全套模板").glob("*[[]STR[]]*.docx"))
    zhang = ctx.data["zhang"]
    upload = zhang.client.post("/files", files={"file": (source.name, source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"purpose": "attachment", "projectId": ctx.data["project"]["id"]},
                               headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"schemec-str-{uuid.uuid4()}"})
    assert upload.status_code == 201, upload.text
    return open_mode(zhang, zhang.call("POST", f"/projects/{ctx.data['project']['id']}/documents",
                      json={"name": f"{name}-{time.strftime('%H%M%S')}", "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]}))["id"]


STR_TABLE_START = "| 测试轮次 | 测试轮次 | 问题总计 | 问题类型 |"
STR_OTHER = "本条应给出测试环境与操作环境的差异"
STR_CAPTION = "表-X按问题类型统计"


def str_table(markdown: str) -> str:
    start = markdown.index(STR_TABLE_START)
    end = markdown.find("\n\n", start)
    return markdown[start:end if end >= 0 else len(markdown)]


def merged_revert_round(ctx: Run, page, concurrent: bool) -> dict:
    tag = "并发" if concurrent else "单人"
    doc_id = str_doc(ctx, f"复验3合并单元格撤销-{tag}")
    original = ctx.server(doc_id)["markdownContent"]
    ctx.open(page, doc_id)
    sent: list[dict] = []
    page.on("response", lambda r: sent.append({"status": r.status, "body": r.request.post_data or ""})
            if r.request.method == "PATCH" and r.url.endswith("/content") else None)
    other_mark, caption_mark = f"【李工别处的修改{tag}】", "【李工改表题】"
    base.type_after(page, STR_OTHER, other_mark)
    if concurrent:
        base.type_after(page, STR_CAPTION, caption_mark)  # 表题段落里另有修改（设计师 TB3-revert-caption）
        ctx.zhang_edit(doc_id, "本条应对被测试软件的设计、操作或测试提供改进建议", "本条应对被测试软件的设计、操作或测试提供改进建议【张工改】")
    cell = page.locator(".paper-content td", has_text="第一轮回归测试").first
    cell.scroll_into_view_if_needed()
    if cell.get_attribute("data-word-protected") == "1":
        # 合并 codex/document-writing-fixes：原稿预览已把合并单元格渲染为 rowspan/colspan 并设为只读保护，
        # 不再能输入后等服务器 422。改为断言：合并格不可编辑，别处修改照常保存，表格保持原样。
        assert cell.get_attribute("contenteditable") == "false", "合并单元格应为只读保护"
        page.keyboard.press("Control+s")
        base.wait_saved(page, 60000)
        expect(page.get_by_test_id("save-blocked")).to_have_count(0)
        server = ctx.server(doc_id)["markdownContent"]
        assert other_mark in server, "别处的修改没进正文"
        assert str_table(server) == str_table(original), "这张表被改动了"
        if concurrent:
            assert caption_mark in server and "【张工改】" in server, "表题的修改或张工的修改丢了"
        base.shot(page, f"C2-合并单元格只读-{tag}")
        return {"tag": tag, "protected": True, "statuses": [row["status"] for row in sent]}
    cell.click()
    page.keyboard.press("End")
    page.keyboard.type("李工填5")
    page.keyboard.press("Control+s")
    banner = page.get_by_test_id("save-blocked")
    expect(banner).to_be_visible(timeout=30000)
    expect(banner).to_contain_text("合并单元格")
    rejected = [row for row in sent if row["status"] == 422]
    assert rejected and other_mark in rejected[-1]["body"] and "李工填5" in rejected[-1]["body"], "前置条件：被拒的那次保存里要同时有别处的修改和合并单元格的修改"
    base.shot(page, f"C2-合并单元格撤销-{tag}-1-被拒")
    banner.get_by_test_id("revert-block").click()
    expect(banner).to_have_count(0, timeout=30000)
    base.wait_saved(page, 30000)
    body = page.locator("body").inner_text()
    assert "没能自动找到这一段" not in body, "撤销提示“没能自动找到这一段”"
    expect(page.get_by_text("已撤销这一段的修改，其余修改已保存。")).to_be_visible(timeout=10000)
    base.shot(page, f"C2-合并单元格撤销-{tag}-2-撤销后")
    server = ctx.server(doc_id)["markdownContent"]
    assert other_mark in server, "别处的修改没进正文"
    assert "李工填5" not in server, "合并单元格里的修改没撤掉"
    assert str_table(server) == str_table(original), "这张表没还原成原样"
    if concurrent:
        assert caption_mark in server and "【张工改】" in server, "表题的修改或张工的修改丢了"
    assert not page.locator(".paper-content td", has_text="李工填5").count(), "编辑区里那一格没还原"
    # 之后再改别处，自动保存照常通过
    base.type_after(page, STR_OTHER, "（之后再改）")
    expect(page.get_by_test_id("save-status")).not_to_contain_text("刚刚保存", timeout=10000)
    base.wait_saved(page, 60000)
    expect(banner).to_have_count(0)
    assert "（之后再改）" in ctx.server(doc_id)["markdownContent"]
    return {"tag": tag, "statuses": [row["status"] for row in sent]}


@scenario("C-2 合并单元格被拒 撤销这一段 别处的修改照常保存")
def s_merged_cell_revert(ctx: Run, page):
    """设计师复验 3 C-2：STR“表-X按问题类型统计”的合并格里打字 + 别处一段修改 → 422 →［撤销这一段的修改］→ 保存 200，别处修改在正文里。
    单人一轮；并发一轮（表题段落也改了，张工同时改了别处）。"""
    out = [merged_revert_round(ctx, page, False)]
    page.context.close()
    page = ctx.page()
    try:
        out.append(merged_revert_round(ctx, page, True))
    finally:
        page.context.close()
    return {"rounds": out}


SCENARIOS += [s_merged_cell_revert]


if __name__ == "__main__":
    sys.exit(main())
