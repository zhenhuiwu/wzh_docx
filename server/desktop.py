"""Portable Windows entry: serve the compiled web app and API in one process."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


RESOURCES = Path(__file__).resolve().parent


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        path = path.replace("\\", "/").lstrip("/")
        if path.startswith(("api/", "_desktop/")) or path in {"api", "_desktop"}:
            raise HTTPException(404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def initialize_admin():
    from app.db import connect, migrate
    from app.identity import create_user, hash_password

    migrate()
    with connect() as connection:
        with connection.transaction(immediate=True):
            if connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]:
                return
            user = create_user(account="admin", name="管理员", password=secrets.token_urlsafe(24),
                               roles=["system_admin"], connection=connection)
            # Preserve the user's fixed initial account without changing password validation.
            connection.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password("admin123"), user))


def application(home: Path):
    from app.main import create_app

    app = create_app()
    installation = hashlib.sha256(str(home.resolve()).encode()).hexdigest()

    @app.get("/_desktop/status", include_in_schema=False)
    def desktop_status():
        return {"installation": installation}

    frontend = RESOURCES / "web" if getattr(sys, "frozen", False) else RESOURCES.parent / "web" / "dist"
    app.mount("/", FrontendFiles(directory=frontend, html=True), name="frontend")
    return app


def self_check():
    from importlib.metadata import version
    import markdown
    import pywintypes
    import tiktoken
    from deepagents import create_deep_agent
    from langchain_openai import ChatOpenAI
    from playwright.sync_api import sync_playwright
    from app.document_agent import _skill_files
    from app.db import migrations_dir

    assert "<table>" in markdown.markdown("| A | B |\n|---|---|\n| 1 | 2 |", extensions=["tables"])
    assert _skill_files("srs") and _skill_files("sdd")
    for encoding in ("cl100k_base", "o200k_base"):
        assert tiktoken.get_encoding(encoding).encode("部署检查")
    create_deep_agent(model=ChatOpenAI(model="self-check", api_key="self-check", base_url="http://127.0.0.1:1"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        browser.close()
    installed = {}
    for name in ("Word", "Visio"):
        try:
            pywintypes.IID(name + ".Application")
            installed[name] = True
        except pywintypes.com_error:
            installed[name] = False
    print(json.dumps({"ok": True, "deepagents": version("deepagents"), "markdown": version("Markdown"),
                      "sqliteMigrations": len(list(migrations_dir().glob("1*.sql"))),
                      "skills": 2, "chromium": True, "officeInstalled": installed}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="文档工作室：双击启动前端与后端")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--home", type=Path, help="配置、data 和 logs 所在目录")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--render-visio", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间")
    home = (args.home or (Path(sys.executable).parent if getattr(sys, "frozen", False) else RESOURCES)).resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(RESOURCES / "ms-playwright")
        os.environ["TIKTOKEN_CACHE_DIR"] = str(RESOURCES / "tiktoken-cache")
    if args.render_visio:
        from app.visio_render import _native_render
        job = args.render_visio.resolve()
        _native_render(json.loads(job.read_text(encoding="utf-8")), job.parent)
        return 0
    if args.self_check:
        self_check()
        return 0
    url = f"http://localhost:{args.port}"
    installation = hashlib.sha256(str(home).encode()).hexdigest()
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", args.port))
        except OSError:
            try:
                with urlopen(url + "/_desktop/status", timeout=3) as response:
                    same = json.load(response).get("installation") == installation
            except (URLError, ValueError):
                same = False
            if not same:
                raise RuntimeError(f"端口 {args.port} 已被其他服务占用，请先关闭占用程序。")
            if not args.no_browser:
                webbrowser.open(url)
            print("文档工作室已在运行：" + url)
            return 0
    (home / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(home / "logs" / "server.log", encoding="utf-8"), logging.StreamHandler()])
    initialize_admin()
    if not args.no_browser:
        def open_when_ready():
            for _ in range(150):
                try:
                    with urlopen(url + "/api/v1/health", timeout=1) as response:
                        if json.load(response)["data"]["status"] == "ok":
                            webbrowser.open(url)
                            return
                except (URLError, ValueError):
                    pass
                time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    logging.info("文档工作室启动：%s；按 Ctrl+C 停止服务。", url)
    import uvicorn
    uvicorn.run(application(home), host="127.0.0.1", port=args.port, log_config=None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("启动失败，请检查配置、日志和端口占用。")
        if not any(arg in sys.argv for arg in ("--no-browser", "--self-check", "--render-visio")):
            input("按回车关闭窗口。")
        raise SystemExit(1)
