"""增量审查 M1（99f45d7）：平移不了时的显式基准，以及超时续打、打字时 409 的浏览器回归。

移植自代码审查员的 p7-fh2-probe.py（只用本机 E2E_API/E2E_WEB 指定的服务，DATABASE_URL 指向同一个库）。
每个场景：在途保存期间张工改了别的段，我继续打字；断言
  - 自己已保存的内容不进冲突（非真冲突场景冲突条数为 0；真冲突只有对方也改过的那一段，“对方”是张工）；
  - 每个标记在服务器正文里恰好出现在预期的段落，且仍在编辑区里；同一个保存键从不换内容。
环境变量 ONLY=场景名（逗号分隔）只跑部分场景。
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
import e2e_phase2 as base  # noqa: E402
import e2e_selfconf as selfconf  # noqa: E402

PARAS = ["甲段：内容甲。", "无。", "乙段：内容乙。", "无。", "丙段：内容丙。", "无。", "丁段：内容丁。", "戊段：内容戊。", "己段：内容己。"]
INIT = r"""
(() => {
  window.__hold = false; window.__respDelay = 0; window.__patches = []; window.__dropResp = 0;
  const of = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = ((init && init.method) || 'GET').toUpperCase();
    if (method === 'PATCH' && /\/documents\/[^/]+\/content$/.test(url)) {
      let body = {}; try { body = JSON.parse(init.body); } catch (e) {}
      const rec = { key: body.clientSaveKey, base: body.expectedRevision, md: body.markdownContent, explicit: typeof body.baseMarkdown === 'string' };
      window.__patches.push(rec);
      while (window.__hold) await new Promise(r => setTimeout(r, 20));
      const resp = await of(input, init);
      rec.status = resp.status;
      if (window.__dropResp > 0) { window.__dropResp--; rec.status = 'dropped-' + resp.status; throw new TypeError('Failed to fetch'); }
      if (window.__respDelay) await new Promise(r => setTimeout(r, window.__respDelay));
      return resp;
    }
    return of(input, init);
  };
})();
"""


class Ctx:
    def __init__(self, data: dict):
        self.data = data
        self.zhang = data["zhang"]

    def zpatch(self, doc_id: str, fn) -> None:
        current = self.zhang.call("GET", f"/documents/{doc_id}")
        text = fn(current["markdownContent"])
        assert text != current["markdownContent"], "张工的修改没有生效"
        self.zhang.call("PATCH", f"/documents/{doc_id}/content", json={"expectedRevision": current["revision"], "markdownContent": text,
                                                                       "allowMerge": True, "clientSaveKey": str(uuid.uuid4())})

    def open_doc(self, page, name: str, markdown: str | None = None) -> str:
        selfconf.PARAS = PARAS
        doc_id = selfconf.make_doc(self.data, f"{name}-{time.strftime('%H%M%S')}", 0)
        if markdown is not None:
            self.zpatch(doc_id, lambda text: text.split("\n\n")[0] + "\n\n" + markdown)
        page.goto(f"{base.WEB}/projects/{self.data['project']['id']}/documents/{doc_id}")
        expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
        base.wait_saved(page, 60000)
        page.wait_for_timeout(800)
        return doc_id


def click(page, text: str, nth: int = 0, tag: str = "p") -> None:
    expect(page.locator(".paper-content")).to_have_attribute("contenteditable", "true", timeout=30000)
    exact = re.compile("^" + re.escape(text) + "$") if text == "无。" else text
    target = page.locator(f".paper-content {tag}", has_text=exact).nth(nth)
    target.scroll_into_view_if_needed()
    target.click()
    page.keyboard.press("End")


def inflight(ctx: Ctx, page, doc_id: str, change, delay: int = 2500) -> None:
    """Ctrl+S 挂起保存请求，张工改，放行并让响应晚 delay 毫秒回来。"""
    count = page.evaluate("window.__patches.length")
    page.evaluate("window.__hold = true")
    page.keyboard.press("Control+s")
    page.wait_for_function(f"window.__patches.length > {count}", timeout=10000)
    ctx.zpatch(doc_id, change)
    page.evaluate(f"window.__respDelay = {delay}; window.__hold = false")


def settle(page, ms: int = 6000) -> None:
    page.wait_for_timeout(ms)
    page.evaluate("window.__respDelay = 0")
    for _ in range(3):
        try:
            base.wait_saved(page, 30000)
        except AssertionError:
            pass
        page.wait_for_timeout(3500)


def conflict_items(doc_id: str) -> list[dict]:
    from app.db import connect
    out = []
    with connect() as connection:
        for row in connection.execute("SELECT id, payload_json FROM document_conflicts WHERE document_id=%s", (doc_id,)).fetchall():
            for item in json.loads(row["payload_json"]).get("conflicts", []):
                theirs = item.get("theirs") or {}
                mine = (item.get("mine") or {}).get("markdown") or ""
                # 方案 C：冲突后续写存成快照，“我的版本”以最新一份为准
                snap = connection.execute(
                    "SELECT markdown FROM document_conflict_mine_snapshots WHERE conflict_id=%s AND item_index=%s ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (row["id"], item.get("itemIndex"))).fetchone()
                out.append({"theirs": theirs.get("markdown") or "", "by": (theirs.get("updatedBy") or {}).get("name"),
                            "mine": mine, "latest": snap["markdown"] if snap else mine})
    return out


def check(ctx: Ctx, name: str, page, doc_id: str, owners: dict[str, str], genuine: tuple[str, str] | None = None,
          max_conflicts: int = 0) -> tuple[str, bool, str]:
    """owners：标记 → 应在的段落开头；genuine：(对方改过的段落开头, 我在该段打的标记) 允许恰好一处真冲突。"""
    markdown = ctx.zhang.call("GET", f"/documents/{doc_id}")["markdownContent"]
    blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
    problems = []
    for marker, owner in owners.items():
        hits = [block for block in blocks if marker in block]
        if len(hits) != 1 or owner not in hits[0].split(marker)[0] and not hits[0].lstrip("-| ").startswith(owner):
            problems.append(f"{marker}→{[hit[:30] for hit in hits]}")
    items = conflict_items(doc_id)
    refresh = page.get_by_role("button", name="立即刷新")
    if genuine and refresh.count():
        # 方案 C：有冻结的冲突段时，合并进来的他人修改要点“立即刷新”才显示（冲突段保持冻结）
        refresh.first.click()
        page.wait_for_timeout(3000)
    editor = page.locator(".paper-content").inner_text()
    # 方案 C：真冲突那一段在编辑区冻结、显示我的版本（对方版本在正文和冲突卡里），所以只属于对方版本的标记不要求出现在编辑区
    frozen_theirs = [marker for marker in owners if genuine and len(items) == 1 and marker in items[0]["theirs"] and marker not in items[0]["latest"]]
    missing = [marker for marker in owners if marker not in editor and marker not in frozen_theirs]
    if missing:
        problems.append(f"编辑区缺 {missing}")
    if genuine and genuine[1] not in editor:
        problems.append(f"编辑区冻结段缺我的 {genuine[1]}")
    patches = page.evaluate("window.__patches")
    reused = [key for key in {p["key"] for p in patches} if len({p["md"] for p in patches if p["key"] == key}) > 1]
    if reused:
        problems.append("同一保存键换了内容")
    if genuine:
        owner, marker = genuine
        if len(items) != 1 or not items[0]["theirs"].startswith(owner) or items[0]["by"] != "张工" or marker not in items[0]["latest"]:
            problems.append(f"真冲突不符：{items}")
        if any(m in item["mine"] for item in items for m in owners):
            problems.append("自己已保存的内容进了冲突")
    elif len(items) > max_conflicts:
        problems.append(f"冲突 {len(items)} 条：{items}")
    page.screenshot(path=str(base.SHOTS / f"fallback-{name}.png"))
    detail = "; ".join(problems) or f"冲突 {len(items)} 条，保存 {[(p['base'], p['status'], p['explicit']) for p in patches]}"
    return name, not problems, detail


def s_multi_fallback(ctx: Ctx, page):
    d = ctx.open_doc(page, "多段回退")
    click(page, "丙段：内容丙。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, lambda md: md.replace("丁段：内容丁。", "丁段：内容丁（张改）。"), delay=5000)
    page.keyboard.type("【P2同段】", delay=60)
    click(page, "己段：内容己。"); page.keyboard.type("【P3在己】", delay=60)
    settle(page, 9000)
    return check(ctx, "multi_fallback", page, d, {"【P1】": "丙段", "【P2同段】": "丙段", "【P3在己】": "己段", "（张改）": "丁段"})


def s_dup_fallback(ctx: Ctx, page):
    d = ctx.open_doc(page, "重复无回退")
    click(page, "乙段：内容乙。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, lambda md: md.replace("丙段：内容丙。\n\n无。", "丙段：内容丙。\n\n无。\n\n无。"))
    page.keyboard.type("【P2乙】", delay=100)
    page.wait_for_timeout(2000)
    click(page, "无。", 2); page.keyboard.type("【P3无】", delay=80)
    settle(page)
    return check(ctx, "dup_fallback", page, d, {"【P1】": "乙段", "【P2乙】": "乙段", "【P3无】": "无。"})


def s_fallback_genuine(ctx: Ctx, page):
    d = ctx.open_doc(page, "回退真冲突")
    click(page, "乙段：内容乙。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, lambda md: md.replace("丁段：内容丁。", "丁段：内容丁（张改）。"))
    page.keyboard.type("【P2乙】", delay=100)
    page.wait_for_timeout(2000)
    click(page, "丁段：内容丁"); page.keyboard.type("【P3丁】", delay=80)
    settle(page)
    return check(ctx, "fallback_genuine", page, d, {"【P1】": "乙段", "【P2乙】": "乙段", "（张改）": "丁段"}, genuine=("丁段：内容丁（张改）", "【P3丁】"))


def s_mine_modified(ctx: Ctx, page):
    d = ctx.open_doc(page, "对方改我段")
    click(page, "丙段：内容丙。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, lambda md: md.replace("丁段：内容丁。", "丁段：内容丁（张改）。"))
    click(page, "丁段：内容丁"); page.keyboard.type("【P2在丁】", delay=100)
    page.wait_for_timeout(1500); click(page, "丙段：内容丙"); page.keyboard.type("【P3丙】", delay=80)
    settle(page)
    return check(ctx, "mine_modified", page, d, {"【P1】": "丙段", "【P3丙】": "丙段", "（张改）": "丁段"}, genuine=("丁段：内容丁（张改）", "【P2在丁】"))


# 编辑区序列化的格式（导入文档首次经编辑区保存后即为此格式）
LIST = "引段：内容引。\n\n-   项一：内容一。\n-   项二：内容二。\n-   项三：内容三。\n\n尾段：内容尾。"
TABLE = "引段：内容引。\n\n| 列甲 | 列乙 |\n|---|---|\n| 行一甲 | 行一乙 |\n| 行二甲 | 行二乙 |\n\n尾段：内容尾。"


def _structured(ctx: Ctx, page, name: str, markdown: str, change, tag: str, cell: str):
    """引段保存在途时张工改了列表/表格的某一项；我在引段续打（建立平移），再去改列表/表格里的 cell。"""
    d = ctx.open_doc(page, name, markdown)
    click(page, "引段：内容引。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, change)
    page.keyboard.type("【P2】", delay=100)
    page.wait_for_timeout(2000)
    click(page, cell, tag=tag); page.keyboard.type("【P3】", delay=80)
    settle(page)
    return d


def s_list_other(ctx: Ctx, page):
    d = _structured(ctx, page, "列表另一项", LIST, lambda md: md.replace("项三：内容三。", "项三：内容三（张改）。"), "li", "项一：内容一。")
    return check(ctx, "list_other", page, d, {"【P1】【P2】": "引段", "【P3】": "项一", "（张改）": "项三"})


def s_table_other(ctx: Ctx, page):
    d = _structured(ctx, page, "表格另一行", TABLE, lambda md: md.replace("行二乙", "行二乙（张改）"), "td", "行一甲")
    return check(ctx, "table_other", page, d, {"【P1】【P2】": "引段", "【P3】": "行一甲", "（张改）": "行二甲"})


def s_table_same_row(ctx: Ctx, page):
    """同一行的另一格被对方改：平移不了，走显式基准；服务器按整张表为一段合并，表格进真冲突（对方是张工），引段不进冲突。"""
    d = _structured(ctx, page, "表格同一行", TABLE, lambda md: md.replace("行一乙", "行一乙（张改）"), "td", "行一甲")
    return check(ctx, "table_same_row", page, d, {"【P1】【P2】": "引段", "（张改）": "行一甲"}, genuine=("| 列甲", "【P3】"))


def s_timeout_typing(ctx: Ctx, page):
    d = ctx.open_doc(page, "超时续打")
    click(page, "丙段：内容丙。"); page.keyboard.type("【P1】")
    page.evaluate("window.__dropResp = 1")
    page.keyboard.press("Control+s")
    page.keyboard.type("【P2】", delay=100)
    settle(page, 12000)
    return check(ctx, "timeout_typing", page, d, {"【P1】【P2】": "丙段"})


def s_conflict409(ctx: Ctx, page):
    d = ctx.open_doc(page, "冲突打字")
    click(page, "丙段：内容丙。"); page.keyboard.type("【P1】")
    inflight(ctx, page, d, lambda md: md.replace("丙段：内容丙。", "丙段：内容丙（张改）。").replace("戊段：内容戊。", "戊段：内容戊（张改戊）。"))
    click(page, "己段：内容己。"); page.keyboard.type("【P2在己】", delay=100)
    settle(page, 9000)
    # 同一处冲突只记一条
    return check(ctx, "conflict409", page, d, {"【P2在己】": "己段", "（张改戊）": "戊段"}, genuine=("丙段：内容丙（张改）", "【P1】"))


SCENARIOS = [s_multi_fallback, s_dup_fallback, s_fallback_genuine, s_mine_modified, s_list_other, s_table_other, s_table_same_row,
             s_timeout_typing, s_conflict409]


def main() -> int:
    base.require_targets()
    data = base.seed()
    sys.path.insert(0, str(base.ROOT / "server"))
    ctx = Ctx(data)
    only = [item for item in os.environ.get("ONLY", "").split(",") if item]
    base.SHOTS.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for scenario in SCENARIOS:
            name = scenario.__name__[2:]
            if only and name not in only:
                continue
            context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
            context.add_init_script(INIT)
            page = context.new_page()
            base.login(page, f"e2e-li-{base.RUN}")
            try:
                results.append(scenario(ctx, page))
            except Exception as error:  # noqa: BLE001 - 记录并继续下一个场景
                results.append((name, False, f"异常：{str(error)[:300]}"))
            context.close()
            print(("PASS " if results[-1][1] else "FAIL ") + results[-1][0] + "：" + results[-1][2], flush=True)
        browser.close()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"e2e_fallback {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
