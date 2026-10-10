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
    expect(page.locator(".wb-notice", has_text="已用你的版本（含续写）更新这一段。")).to_be_visible(timeout=5000)
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
STRETCH_LOCK = """(() => { const raw = window.setTimeout; window.setTimeout = (fn, ms, ...rest) => raw(fn, ms === 250 ? 2500 : ms, ...rest); })()"""


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


SCENARIOS = [s_repro, s_dom, s_other, s_keep_mine, s_use_theirs, s_keep_both, s_manual, s_leave, s_reload_draft, s_reconflict, s_copy, s_copy_export_ai, s_tm2,
             s_timing_small, s_timing_big, s_tablist, s_load_lock, s_self_theirs, s_list_batches, s_offline_reopen]


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


if __name__ == "__main__":
    sys.exit(main())
