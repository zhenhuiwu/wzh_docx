"""界面复验 N1/N2 自测：6 个 438C 模板里只删“文档标识号”这一段（不碰相邻标题）→ 保存被拒 →
“撤销这一段的修改”按原内容插回原位置 → 保存成功、编辑区重绘出这一行 → 之后再输入、自动保存也不会把它删掉。每个模板 3 次。

运行（先用 /tmp/wzh_phase2_run/live/start.sh 起服务；环境变量要求同 e2e_phase2.py）：
    E2E_API=http://127.0.0.1:8041/api/v1 E2E_WEB=http://127.0.0.1:5211 E2E_SHOTS=/workspace/wzh_collab/screens/phase2 \
    DATABASE_URL=sqlite:////tmp/wzh_phase2_run/live/app.db FILE_DATA_DIR=/tmp/wzh_phase2_run/live/files \\
        uv run python tests/e2e_identifier_revert.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import e2e_phase2 as E  # noqa: E402

TEMPLATES = ("SDP", "STP", "IDD", "SRS", "SDD", "DBDD")
ROUNDS = 3


def upload(data: dict, tag: str) -> dict:
    zhang = data["zhang"]
    source = next((E.ROOT / "438C-2021全套模板").glob(f"*[[]{tag}[]]*.docx"))
    response = zhang.client.post("/files", files={"file": (source.name, source.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                                 data={"purpose": "attachment", "projectId": data["project"]["id"]},
                                 headers={"X-CSRF-Token": zhang.csrf, "Idempotency-Key": f"e2e-id-{tag}-{E.RUN}"})
    assert response.status_code == 201, response.text
    return zhang.call("POST", f"/projects/{data['project']['id']}/documents",
                      json={"name": f"标识号撤销-{tag}", "sourceType": "upload", "wordFileId": response.json()["data"]["id"]})


DELETE_IDENTIFIER = """() => {
  const node = [...document.querySelectorAll('.paper-content > p')].find(p => p.textContent.includes('文档标识号'));
  if (!node) return false;
  node.remove();
  document.querySelector('.paper-content').dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}"""


def main() -> int:
    E.require_targets()
    data = E.seed()
    documents = {tag: upload(data, tag) for tag in TEMPLATES}
    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(viewport={"width": 1920, "height": 1080}, locale="zh-CN").new_page()
        E.login(page, f"e2e-zhang-{E.RUN}")
        for tag, document in documents.items():
            original = data["zhang"].call("GET", f"/documents/{document['id']}")["markdownContent"]
            other = E.pick_paragraphs(original, 1)[0]
            page.goto(f"{E.WEB}/projects/{data['project']['id']}/documents/{document['id']}")
            expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
            expect(page.get_by_test_id("save-status")).to_contain_text("保存", timeout=30000)
            for attempt in range(1, ROUNDS + 1):
                label = f"{tag} 第 {attempt} 次"
                try:
                    expect(page.locator(".paper-content")).to_have_attribute("contenteditable", "true", timeout=30000)
                    assert page.evaluate(DELETE_IDENTIFIER), "编辑区里没有“文档标识号”段"
                    E.save_now(page)
                    banner = page.get_by_test_id("save-blocked")
                    expect(banner).to_be_visible(timeout=20000)
                    expect(banner).to_contain_text("书签")
                    if tag == "IDD" and attempt == 1:
                        E.shot(page, "27-删除标识号被拒-可撤销")
                    banner.get_by_test_id("revert-block").click()
                    expect(banner).to_have_count(0, timeout=20000)
                    E.wait_saved(page)
                    # N2：编辑区必须重绘出这一行
                    expect(page.locator(".paper-content > p", has_text="文档标识号")).to_have_count(1, timeout=20000)
                    server = data["zhang"].call("GET", f"/documents/{document['id']}")["markdownContent"]
                    assert "文档标识号" in server, "撤销后服务器正文缺“文档标识号”"
                    # 再输入一处并自动保存，不能把这一行重新删掉
                    E.type_after(page, other, f"（{tag}-{attempt}）")
                    expect(page.get_by_test_id("save-status")).not_to_contain_text("刚刚保存", timeout=10000)
                    E.wait_saved(page, 60000)  # 自动保存
                    expect(page.get_by_test_id("save-blocked")).to_have_count(0)
                    server = data["zhang"].call("GET", f"/documents/{document['id']}")["markdownContent"]
                    assert "文档标识号" in server and f"（{tag}-{attempt}）" in server
                    assert server.replace(f"（{tag}-{attempt}）", "").count("文档标识号") == original.count("文档标识号")
                    print(f"PASS {label}", flush=True)
                except Exception as error:  # noqa: BLE001
                    failures += 1
                    E.OUT.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(E.OUT / f"identifier-{tag}-{attempt}.png"))
                    print(f"FAIL {label}: {(str(error).splitlines() or [repr(error)])[0][:300]}", flush=True)
                    page.reload()
                    expect(page.locator(".paper-content")).to_be_visible(timeout=60000)
            final = data["zhang"].call("GET", f"/documents/{document['id']}")["markdownContent"]
            if final.count("文档标识号") != original.count("文档标识号"):
                failures += 1
                print(f"FAIL {tag} 最终正文“文档标识号”行数不对", flush=True)
        browser.close()
    total = len(TEMPLATES) * ROUNDS
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
