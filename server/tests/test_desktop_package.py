"""One portable-entry check: routes, the fixed initial account and existing data."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import desktop
from app.config import clear_settings_cache
from app.db import connect
from app.identity import verify_password


def test_portable_routes_and_initial_account(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<title>文档工作室</title>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", desktop.FrontendFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        assert client.get("/login?redirect=/projects").status_code == 200
        assert client.get("/projects/a/documents/b").status_code == 200
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/v1/missing").status_code == 404
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "app.db"))
    clear_settings_cache()
    try:
        desktop.initialize_admin()
        with connect() as connection:
            original = dict(connection.execute("SELECT * FROM users").fetchone())
            assert original["account"] == "admin"
            assert verify_password("admin123", original["password_hash"])
        desktop.initialize_admin()
        with connect() as connection:
            assert dict(connection.execute("SELECT * FROM users").fetchone()) == original
    finally:
        clear_settings_cache()
