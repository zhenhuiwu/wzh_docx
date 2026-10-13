"""已启动项目的原稿滚动验收，不修改文档。

server/.venv/Scripts/python.exe web/tests/word-preview-scroll.py DOCUMENT_ID --output SCREENSHOT_DIR
需选用至少三页的现有 Word；使用独立浏览器会话。
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect


async def check(document_id: str, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN")
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://localhost:5173/login")
        await page.locator("#staff-id").fill(os.getenv("DOCWB_UI_USER", "admin"))
        await page.locator("#password").fill(os.getenv("DOCWB_UI_PASSWORD", "admin123"))
        await page.get_by_role("button", name="登录", exact=True).click()
        await expect(page).to_have_url("http://localhost:5173/projects")
        record_url = f"http://localhost:5173/api/v1/documents/{document_id}"
        before = (await (await context.request.get(record_url)).json())["data"]
        await page.goto(f'http://localhost:5173/projects/{before["projectId"]}/documents/{document_id}')
        editor = page.get_by_role("textbox", name="文档正文", exact=True)
        await expect(editor).to_have_attribute("contenteditable", "true", timeout=30000)
        edit_width = (await page.locator(".paper").bounding_box())["width"]
        checks = {}
        for button, mode in [("查看原稿", "original"), ("版式预览", "layout")]:
            await page.get_by_role("button", name=button, exact=True).click()
            scroll = page.get_by_role("region", name=f'Word {"原稿" if mode == "original" else "编辑稿"}', exact=True)
            await expect(scroll).to_be_visible(timeout=120000)
            canvases = scroll.locator("canvas")
            count = await canvases.count()
            assert count >= 3, "请选用至少三页的 Word"
            await expect(canvases.first).to_have_attribute("aria-busy", "false", timeout=30000)
            assert abs((await canvases.first.bounding_box())["width"] - edit_width) < 1
            assert await page.get_by_role("button", name="下一页", exact=True).count() == 0
            await page.screenshot(path=str(output / f"{mode}-first.png"))
            box = await scroll.bounding_box()
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            # 真正滚轮跨页，页间位置保持在同一个滚动容器。
            await page.mouse.wheel(0, 650)
            await page.wait_for_function('document.querySelector(".pdf-scroll").scrollTop > 600')
            await expect(canvases.nth(1)).to_have_attribute("aria-busy", "false")
            await page.screenshot(path=str(output / f"{mode}-page-boundary.png"))
            seen = []
            for index in range(count):
                canvas = canvases.nth(index)
                await canvas.scroll_into_view_if_needed()
                await expect(canvas).to_have_attribute("aria-busy", "false", timeout=30000)
                # 确认真正绘制，而非只有页码/白色占位。
                assert await canvas.evaluate("""el => {
                  const pixels = el.getContext('2d').getImageData(0, 0, el.width, el.height).data;
                  for (let i = 0; i < pixels.length; i += 16) if (pixels[i + 3] && pixels[i] < 200) return true;
                  return false;
                }"""), f"第 {index + 1} 页未绘制"
                seen.append(index + 1)
            await page.screenshot(path=str(output / f"{mode}-last.png"))
            assert await canvases.first.evaluate("el => el.width") == 0, "远处画布未释放"
            allocated = await canvases.evaluate_all("els => els.filter(el => el.width > 0).length")
            assert allocated < count
            # 键盘滚动与回到首屏后重绘。
            await scroll.focus()
            await page.keyboard.press("Control+Home")
            await page.wait_for_function('document.querySelector(".pdf-scroll").scrollTop === 0')
            await expect(canvases.first).to_have_attribute("aria-busy", "false", timeout=30000)
            await page.keyboard.press("PageDown")
            await page.wait_for_function('document.querySelector(".pdf-scroll").scrollTop > 0')
            assert await scroll.evaluate("el => el.matches(':focus-visible') && getComputedStyle(el).outlineStyle === 'solid'")
            await page.screenshot(path=str(output / f"{mode}-keyboard-focus.png"))
            await page.keyboard.press("Control+Home")
            await page.wait_for_function('document.querySelector(".pdf-scroll").scrollTop === 0')
            await page.screenshot(path=str(output / f"{mode}-returned.png"))
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            checks[mode] = {"pagesDrawn": seen, "allocatedAtEnd": allocated, "width": edit_width}
        await page.get_by_role("button", name="编辑稿", exact=True).click()
        await expect(editor).to_be_visible()
        preview_url = f"**/api/v1/documents/{document_id}/word-preview*"
        await page.route(preview_url, lambda route: route.fulfill(status=503, json={"error": {"message": "测试预览失败"}}), times=1)
        await page.get_by_role("button", name="查看原稿", exact=True).click()
        await expect(page.get_by_role("alert").filter(has_text="测试预览失败")).to_be_visible()
        await page.screenshot(path=str(output / "original-error.png"))
        await page.get_by_role("button", name="重试预览", exact=True).click()
        await expect(page.locator('.pdf-scroll canvas').first).to_have_attribute("aria-busy", "false", timeout=120000)
        # 载入期间切回编辑，过时请求不能覆盖新视图。
        await page.get_by_role("button", name="编辑稿", exact=True).click()
        requested = asyncio.Event()
        released = asyncio.Event()

        async def delayed(route):
            requested.set()
            await released.wait()
            await route.continue_()

        await page.route(preview_url, delayed, times=1)
        await page.get_by_role("button", name="查看原稿", exact=True).click()
        await asyncio.wait_for(requested.wait(), timeout=10)
        await page.get_by_role("button", name="编辑稿", exact=True).click()
        released.set()
        await expect(editor).to_be_visible()
        await page.get_by_role("button", name="查看原稿", exact=True).click()
        await expect(page.locator('.pdf-scroll canvas').first).to_have_attribute("aria-busy", "false", timeout=120000)
        await page.get_by_role("button", name="编辑稿", exact=True).click()
        after = (await (await context.request.get(record_url)).json())["data"]
        assert before == after, "验收改变了文档"
        assert not errors, errors
        (output / "checks.json").write_text(json.dumps({"viewport": [1920, 1080], "modes": checks, "retry": True, "cancel": True, "documentUnchanged": True, "browserErrors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
        session = (await (await context.request.get("http://localhost:5173/api/v1/auth/me")).json())["data"]
        await context.request.post("http://localhost:5173/api/v1/auth/logout", headers={"x-csrf-token": session["csrfToken"]})
        await browser.close()
    print("PASS: continuous native Word pages, mouse/keyboard scroll, canvas recycling, retry, cancellation, unchanged document")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_id")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.document_id, args.output))
