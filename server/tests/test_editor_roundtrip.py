"""F1（01 §6.0 / 用例 31–33）：编辑区 HTML → 浏览器内 editorMarkdown → 服务器 Markdown 往返。

需要 Node 22（打包 web/tests/editor-roundtrip）与 Playwright Chromium；默认跳过，
设置 DOCWB_E2E=1 运行：
    DOCWB_E2E=1 DOCWB_TEST_DATABASE_URL=sqlite:////tmp/x.db uv run pytest tests/test_editor_roundtrip.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "438C-2021全套模板").glob("*.docx"))
pytestmark = pytest.mark.skipif(os.environ.get("DOCWB_E2E") != "1", reason="浏览器往返测试需 DOCWB_E2E=1")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from playwright.sync_api import sync_playwright
    node = shutil.which("node")
    if node is None:
        pytest.skip("需要 Node 22")
    bundle = tmp_path_factory.mktemp("roundtrip") / "roundtrip.js"
    subprocess.run([node, str(ROOT / "web/tests/editor-roundtrip/build.mjs"), str(bundle)], check=True, cwd=ROOT / "web")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_content("<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>")
        page.add_script_tag(content=bundle.read_text(encoding="utf-8"))
        yield page
        browser.close()


def _editor_html(path: Path):
    from app.docx_parser import parse_docx
    from app.docx_preview import render_document_html
    from app.document_selection import decorate_selection_html
    parsed = parse_docx(str(path))
    return parsed, decorate_selection_html(parsed.markdown, render_document_html(path, parsed.markdown, parsed.images))


def _check(markdown: str, path: Path, images: list[dict]) -> Path:
    from app.docx_export import diagram_images, edit_original_docx
    directory = TemporaryDirectory()
    output = Path(directory.name) / "checked.docx"
    edit_original_docx(markdown, path, output, image_sources=diagram_images(images))
    _check.keep.append(directory)
    return output


_check.keep = []  # type: ignore[attr-defined]


def _tables_xml(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    return re.findall(r"<w:tbl>.*?</w:tbl>", body, flags=re.S)


@pytest.mark.parametrize("path", TEMPLATES, ids=[p.name[:12] for p in TEMPLATES])
def test_template_roundtrip_is_identity(page, path):
    parsed, html = _editor_html(path)
    markdown = page.evaluate("([h, i]) => window.roundtrip(h, i)", [html, parsed.images])
    # 测试 L4：编辑区保留行内制表符，往返必须逐字节一致（不再容忍 \t→空格）。
    assert markdown == parsed.markdown
    _check(markdown, path, parsed.images)


def test_srs_paragraph_edit_keeps_cover_table(page):
    path = next(p for p in TEMPLATES if "[SRS]" in p.name)
    parsed, html = _editor_html(path)
    original = _check(parsed.markdown, path, parsed.images)
    added = page.evaluate(
        """([h, i]) => {
          window.roundtrip(h, i);
          const root = document.body.firstElementChild;
          const heading = [...root.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(x => /^\\s*3\\.1\\b|范围|要求的状态和方式/.test(x.textContent) && /3\\.1/.test(x.textContent))
            || [...root.querySelectorAll('h1,h2,h3,h4,h5,h6')][3];
          let block = heading.nextElementSibling;
          while (block && block.tagName !== 'P') block = block.nextElementSibling;
          block.appendChild(document.createTextNode('（往返测试加字）'));
          return window.editorRoundtrip(root, i);
        }""",
        [html, parsed.images],
    )
    assert "（往返测试加字）" in added
    edited = _check(added, path, parsed.images)
    from docx import Document
    assert any("（往返测试加字）" in p.text for p in Document(str(edited)).paragraphs)
    assert _tables_xml(edited)[0] == _tables_xml(original)[0]


@pytest.mark.parametrize("html,expected", [
    ("<table><thead><tr><th>密&nbsp; 级：</th><th>x</th></tr></thead><tbody><tr><td>a|b</td><td>c<br>d</td></tr></tbody></table>",
     "| 密\u00a0 级： | x |\n|---|---|\n| a\\|b | c<br>d |"),
    ("<p>1. 不是列表</p><p>- 也不是</p><p>产品型号-XXXX</p><p>a  b</p>", "1\\. 不是列表\n\n\\- 也不是\n\n产品型号-XXXX\n\na  b"),
])
def test_constructed_dialect_cases(page, html, expected):
    markdown = page.evaluate("([h]) => window.roundtrip(h, [])", [html])
    assert markdown == expected
