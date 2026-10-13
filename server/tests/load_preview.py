"""P0-2 压测：4 人同时打开 360 节大文档（同一篇 / 4 篇不同），测可编辑时间、预览时间、其他接口延迟。

用法（服务须已启动，账号为系统管理员）：
  LOAD_API=http://127.0.0.1:8095/api/v1 LOAD_ACCOUNT=admin LOAD_PASSWORD=... .venv/bin/python tests/load_preview.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.docx_parser import parse_docx  # noqa: E402
from tests.test_preview_large import big_markdown  # noqa: E402

API = os.environ.get("LOAD_API", "http://127.0.0.1:8095/api/v1")
ACCOUNT, PASSWORD = os.environ.get("LOAD_ACCOUNT", "admin"), os.environ["LOAD_PASSWORD"]
SOURCE = next((Path(__file__).resolve().parents[2] / "438C-2021全套模板").glob("*[[]SRS[]]*.docx"))
USERS = 4


def session() -> httpx.Client:
    client = httpx.Client(base_url=API, timeout=300)
    token = client.get("/auth/csrf").json()["data"]["csrfToken"]
    response = client.post("/auth/login", json={"account": ACCOUNT, "password": PASSWORD}, headers={"X-CSRF-Token": token})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["data"]["csrfToken"]
    return client


def setup(client: httpx.Client, count: int, tag: str) -> list[str]:
    owner = client.get("/auth/me").json()["data"]["user"]["id"]
    created = client.post("/projects", json={"name": f"压测-{tag}-{uuid.uuid4().hex[:4]}", "ownerId": owner, "status": "active"},
                          headers={"Idempotency-Key": uuid.uuid4().hex})
    assert created.status_code in (200, 201), created.text
    project = created.json()["data"]["id"]
    upload = client.post("/files", files={"file": (SOURCE.name, SOURCE.read_bytes(),
                                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                         data={"purpose": "attachment", "projectId": project}, headers={"Idempotency-Key": uuid.uuid4().hex})
    upload.raise_for_status()
    base = big_markdown(parse_docx(str(SOURCE)).markdown)
    ids = []
    for index in range(count):
        doc = client.post(f"/projects/{project}/documents",
                          json={"name": f"大文档{index}", "sourceType": "upload", "wordFileId": upload.json()["data"]["id"]}).json()["data"]
        saved = client.patch(f"/documents/{doc['id']}/content",
                             json={"expectedRevision": doc["revision"], "markdownContent": base + f"\n\n结尾 {tag} {index}。"})
        saved.raise_for_status()
        ids.append(doc["id"])
    return ids


def open_document(client: httpx.Client, did: str, start: threading.Event) -> dict:
    start.wait()
    began = time.perf_counter()
    assert client.get(f"/documents/{did}").status_code == 200
    assert client.get(f"/documents/{did}/objects").status_code == 200
    editor = time.perf_counter() - began
    preview = client.get(f"/documents/{did}/preview")
    assert preview.status_code == 200, preview.text[:200]
    return {"editor": editor, "preview": time.perf_counter() - began}


def scenario(name: str, clients: list[httpx.Client], ids: list[str]) -> None:
    start, stop, latencies = threading.Event(), threading.Event(), []
    probe = session()

    def ping():
        while not stop.is_set():
            began = time.perf_counter()
            probe.get("/auth/me")
            latencies.append(time.perf_counter() - began)
            time.sleep(0.2)
    pinger = threading.Thread(target=ping)
    pinger.start()
    with ThreadPoolExecutor(USERS) as pool:
        futures = [pool.submit(open_document, clients[i], ids[i], start) for i in range(USERS)]
        start.set()
        results = [future.result() for future in futures]
    stop.set()
    pinger.join()
    editor = [round(r["editor"], 2) for r in results]
    preview = [round(r["preview"], 2) for r in results]
    print(f"{name}: 可编辑 {editor}s（最大 {max(editor)}） 预览 {preview}s（最大 {max(preview)}） "
          f"/auth/me 延迟 最大 {max(latencies):.2f}s 中位 {sorted(latencies)[len(latencies) // 2]:.3f}s（{len(latencies)} 次）")
    assert max(editor) <= 10, "可编辑超过 10 秒"


def main() -> None:
    admin = session()
    clients = [session() for _ in range(USERS)]
    same = setup(admin, 1, "same")
    scenario("同一篇 ×4", clients, same * USERS)
    different = setup(admin, USERS, "diff")
    scenario("4 篇不同", clients, different)


if __name__ == "__main__":
    main()
