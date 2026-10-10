"""保存状态机随机模型测试（测试工程师建议）：随机交替本地打字、保存、保存返回（延迟/丢失/失败）、他人修改（同段或不同段），
结束后断言三条不变量：
  1. 不丢字：我打的每个标记都在服务器正文、未处理冲突的最新“我的版本”（mineLatest，含冲突后续写）或编辑区里；
     我选了“使用对方的版本”的那几处，标记在已处理冲突的 mineLatest 里也算；
  2. 不覆盖对方：张工写进去的每个标记都在服务器正文里（我选了“保留我的”的那几处冲突里对方版本的标记除外）；
  3. 不和自己冲突：每条冲突记录的“对方”都是张工，且冲突的对方版本里确有张工的标记。
方案 C 扩展：冲突后继续在冻结段里打字（type 落到冻结段时即续写），随机用面板“保留我的 / 使用对方的版本 / 两段都保留”处理。
环境变量：RUNS（默认 6）、STEPS（每轮动作数，默认 16）、SEED（默认 20261010）、BIG（填充段落数，默认 0）。
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_fallback as fb  # noqa: E402
import e2e_phase2 as base  # noqa: E402
import e2e_selfconf as selfconf  # noqa: E402

PARAS = ["甲段：内容甲。", "无。", "乙段：内容乙。", "无。", "丙段：内容丙。", "丁段：内容丁。", "戊段：内容戊。", "己段：内容己。"]
TARGETS = ["甲段", "乙段", "丙段", "丁段", "戊段", "己段"]
INIT = fb.INIT.replace("window.__dropResp = 0;", "window.__dropResp = 0; window.__failNext = 0;").replace(
    "      const resp = await of(input, init);",
    "      if (window.__failNext > 0) { window.__failNext--; rec.status = 'neterr'; throw new TypeError('Failed to fetch'); }\n      const resp = await of(input, init);")


def run_once(ctx: fb.Ctx, browser, rnd: random.Random, steps: int, big: int, label: str) -> tuple[bool, dict]:
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
    context.add_init_script(INIT)
    page = context.new_page()
    base.login(page, f"e2e-li-{base.RUN}")
    selfconf.PARAS = PARAS
    doc_id = selfconf.make_doc(ctx.data, f"随机{label}-{time.strftime('%H%M%S')}", big)
    page.goto(f"{base.WEB}/projects/{ctx.data['project']['id']}/documents/{doc_id}")
    page.locator(".paper-content").wait_for(timeout=60000)
    base.wait_saved(page, 60000)
    page.wait_for_timeout(800)
    mine: list[str] = []
    theirs: list[str] = []
    kept_over: list[str] = []  # 我选“保留我的”时替换掉的对方标记
    log: list[str] = []
    for step in range(steps):
        action = rnd.choices(["type", "save", "zhang", "drop", "fail", "delay", "wait", "frozen", "resolve"], [5, 2, 2, 1, 1, 1, 1, 2, 1])[0]
        if action == "frozen":
            # 方案 C：冲突后在冻结段里续写
            frozen_blocks = page.locator(".paper-content [data-frozen]")
            if not frozen_blocks.count():
                action = "type"
            else:
                marker = f"〔{label}{step:02d}〕"
                try:
                    frozen_blocks.first.click()
                    page.keyboard.press("End")
                    page.keyboard.type(marker, delay=rnd.choice([0, 30]))
                    mine.append(marker)
                    log.append(f"frozen {marker}")
                except Exception as error:  # noqa: BLE001
                    log.append(f"frozen skipped: {str(error)[:60]}")
                continue
        if action == "resolve":
            choice = rnd.choice(["保留我的", "使用对方的版本", "两段都保留"])
            try:
                if not page.locator(".conflict-panel").count():
                    if page.get_by_test_id("conflict-toggle").count():
                        page.get_by_test_id("conflict-toggle").click()
                card = page.locator(".conflict-panel .ccard").first
                card.wait_for(timeout=4000)
                theirs_text = card.locator(".ccard-side.theirs").inner_text()
                button = card.get_by_role("button", name=choice)
                if not button.count():
                    log.append(f"resolve {choice} n/a")
                    continue
                button.first.click()
                if choice == "使用对方的版本":
                    card.locator(".ccard-confirm").get_by_role("button", name="使用对方的版本").click()
                page.wait_for_timeout(1500)
                if choice == "保留我的":
                    kept_over.extend(m for m in theirs if m in theirs_text)
                log.append(f"resolve {choice}")
            except Exception as error:  # noqa: BLE001 - 没有待处理冲突时这一步作废
                log.append(f"resolve skipped: {str(error)[:60]}")
            continue
        if action == "type":
            target = rnd.choice(TARGETS)
            marker = f"〔{label}{step:02d}〕"
            try:
                fb.click(page, target)
                page.keyboard.type(marker, delay=rnd.choice([0, 30, 80]))
                mine.append(marker)
                log.append(f"type {target} {marker}")
            except Exception as error:  # noqa: BLE001 - 编辑区暂不可编辑（重绘/锁定）时这一步作废
                log.append(f"type {target} skipped: {str(error)[:60]}")
        elif action == "save":
            page.keyboard.press("Control+s")
            log.append("save")
        elif action == "zhang":
            target = rnd.choice(TARGETS)
            marker = f"「Z{label}{step:02d}」"
            def change(md, t=target, m=marker):
                block = next(b for b in md.split("\n\n") if b.startswith(t))
                return md.replace(block, block + m, 1)
            for attempt in range(3):
                try:
                    ctx.zpatch(doc_id, change)
                    break
                except Exception as error:  # noqa: BLE001 - 读和写之间我刚好存了一版：张工按新版重做（他自己的冲突不计入）
                    if "409" not in str(error) or attempt == 2:
                        raise
            theirs.append(marker)
            log.append(f"zhang {target} {marker}")
        elif action == "drop":
            page.evaluate("window.__dropResp = 1")
            log.append("drop-next-response")
        elif action == "fail":
            page.evaluate("window.__failNext = 1")
            log.append("fail-next-request")
        elif action == "delay":
            ms = rnd.choice([500, 1500, 3000])
            page.evaluate(f"window.__respDelay = {ms}")
            log.append(f"delay {ms}")
        else:
            ms = rnd.choice([100, 400, 1200, 2500])
            page.wait_for_timeout(ms)
            log.append(f"wait {ms}")
    page.evaluate("window.__respDelay = 0; window.__dropResp = 0; window.__failNext = 0")
    fb.settle(page, 8000)
    # 收尾：若还有冲突，必定走一次“冻结段续写 + 处理”，三种处理方式按轮次轮换，保证覆盖
    if page.locator(".paper-content [data-frozen]").count():
        marker = f"〔{label}end〕"
        try:
            page.locator(".paper-content [data-frozen]").first.click()
            page.keyboard.press("End")
            page.keyboard.type(marker, delay=30)
            mine.append(marker)
            log.append(f"frozen {marker}")
            page.wait_for_timeout(2500)
        except Exception as error:  # noqa: BLE001
            log.append(f"frozen skipped: {str(error)[:60]}")
    if page.get_by_test_id("conflict-toggle").count():
        choice = ["保留我的", "使用对方的版本", "两段都保留"][int(label[1:]) % 3]
        try:
            if not page.locator(".conflict-panel").count():
                page.get_by_test_id("conflict-toggle").click()
            card = page.locator(".conflict-panel .ccard").first
            card.wait_for(timeout=8000)
            theirs_text = card.locator(".ccard-side.theirs").inner_text()
            card.get_by_role("button", name=choice).first.click()
            if choice == "使用对方的版本":
                card.locator(".ccard-confirm").get_by_role("button", name="使用对方的版本").click()
            page.wait_for_timeout(2000)
            if choice == "保留我的":
                kept_over.extend(m for m in theirs if m in theirs_text)
            log.append(f"resolve {choice}")
        except Exception as error:  # noqa: BLE001
            log.append(f"resolve skipped: {str(error)[:60]}")
        fb.settle(page, 8000)
    md = ctx.zhang.call("GET", f"/documents/{doc_id}")["markdownContent"]
    editor = page.locator(".paper-content").inner_text()
    from app.db import connect
    with connect() as connection:
        # 只看我（李工）的冲突记录；张工重做时自己产生的冲突不计入
        rows = connection.execute("SELECT c.status, c.payload_json FROM document_conflicts c JOIN users u ON u.id = c.actor_id "
                                  "WHERE c.document_id=%s AND u.name <> '张工'", (doc_id,)).fetchall()
    items = [{"theirs": (item.get("theirs") or {}).get("markdown") or "", "by": ((item.get("theirs") or {}).get("updatedBy") or {}).get("name")}
             for row in rows for item in json.loads(row["payload_json"]).get("conflicts", [])]
    open_mines = "\n".join(((item.get("mine") or {}).get("markdown") or "") for row in rows if row["status"] == "open"
                           for item in json.loads(row["payload_json"]).get("conflicts", []))
    li = base.Api(f"e2e-li-{base.RUN}")
    latest = "\n".join(((item.get("mineLatest") or {}).get("markdown") or "")
                        for item in li.call("GET", f"/documents/{doc_id}/conflicts?status=open&actor=me")["items"])
    discarded = "\n".join(((item.get("mineLatest") or {}).get("markdown") or "")
                           for item in li.call("GET", f"/documents/{doc_id}/conflicts?status=resolved&actor=me")["items"]
                           if item.get("action") in ("use_theirs", "discard"))
    lost = [m for m in mine if m not in md and m not in open_mines and m not in latest and m not in editor and m not in discarded]
    overwritten = [m for m in theirs if m not in md and m not in kept_over]
    self_conflicts = [item for item in items if item["by"] != "张工" or "「Z" not in item["theirs"]]
    ok = not lost and not overwritten and not self_conflicts
    detail = {"label": label, "ok": ok, "lost": lost, "overwritten": overwritten, "selfConflicts": self_conflicts, "conflicts": len(items),
              "mine": len(mine), "theirs": len(theirs), "status": page.get_by_test_id("save-status").inner_text()[:20],
              "frozenTyped": sum(entry.startswith("frozen 〔") for entry in log),
              "resolved": [entry[8:] for entry in log if entry.startswith("resolve ") and "skipped" not in entry and "n/a" not in entry]}
    if not ok or os.environ.get("DUMP"):
        detail["patches"] = page.evaluate("window.__patches.map(p => ({base: p.base, status: p.status, explicit: p.explicit, key: (p.key || '').slice(0, 6), "
                                          "md: p.md.split('\\n\\n').filter(b => /[〔「]/.test(b)).join(' | ')}))")
        detail["log"] = log
        detail["server"] = md[:600]
        page.screenshot(path=str(base.SHOTS / f"random-{label}.png"))
    context.close()
    return ok, detail


def main() -> int:
    base.require_targets()
    data = base.seed()
    ctx = fb.Ctx(data)
    runs, steps = int(os.environ.get("RUNS", "6")), int(os.environ.get("STEPS", "16"))
    seed, big = int(os.environ.get("SEED", "20261010")), int(os.environ.get("BIG", "0"))
    base.SHOTS.mkdir(parents=True, exist_ok=True)
    passed = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for n in range(runs):
            rnd = random.Random(seed + n)
            try:
                ok, detail = run_once(ctx, browser, rnd, steps, big, f"r{n}")
            except Exception as error:  # noqa: BLE001 - 记录并继续
                ok, detail = False, {"label": f"r{n}", "error": str(error)[:300]}
            passed += ok
            print(("PASS " if ok else "FAIL ") + json.dumps(detail, ensure_ascii=False), flush=True)
        browser.close()
    print(f"e2e_random {passed}/{runs}（SEED={seed}，STEPS={steps}，BIG={big}）")
    return 0 if passed == runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
