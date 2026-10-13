"""S1：/capabilities 与 AI 调用点一致（01 §8.2 用例 47）。"""

from fastapi.testclient import TestClient
import pytest
import sys
from types import SimpleNamespace

from app.ai_capability import _office, ai_status, require_ai
from app.config import clear_settings_cache
from app.db import migrate
from app.errors import AppError
from app.identity import create_user
from app.main import create_app

CASES = [
    ({}, False, "not_configured"),
    ({"AI_BASE_URL": "http://127.0.0.1:9/v1"}, False, "not_configured"),
    ({"AI_BASE_URL": "http://127.0.0.1:9/v1", "AI_MODEL": "m"}, True, None),
    ({"AI_BASE_URL": "http://ai.example.com/v1", "AI_MODEL": "m"}, False, "remote_requires_https_key"),
    ({"AI_BASE_URL": "https://ai.example.com/v1", "AI_MODEL": "m"}, False, "remote_requires_https_key"),
    ({"AI_BASE_URL": "https://ai.example.com/v1", "AI_MODEL": "m", "AI_API_KEY": "k"}, True, None),
    ({"AI_BASE_URL": "ftp://ai.example.com", "AI_MODEL": "m"}, False, "invalid_url"),
]


def _env(monkeypatch, values):
    for key in ("AI_BASE_URL", "AI_MODEL", "AI_API_KEY"):
        monkeypatch.setenv(key, values.get(key, ""))
    clear_settings_cache()


@pytest.mark.parametrize(("values", "available", "reason"), CASES)
def test_capabilities_match_call_sites(monkeypatch, values, available, reason):
    monkeypatch.setattr("app.ai_capability._office", lambda name: {"available": False, "reason": f"{name.lower()}_not_installed"})
    _env(monkeypatch, values)
    migrate()
    create_user(account="cap-user", name="普通", password="capabilities-pass", roles=["template_manager"])
    create_user(account="cap-admin", name="管理员", password="capabilities-pass", roles=["system_admin"])
    with TestClient(create_app()) as client:
        for account, admin in (("cap-user", False), ("cap-admin", True)):
            client.cookies.clear()
            token = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
            assert client.post("/api/v1/auth/login", json={"account": account, "password": "capabilities-pass"},
                               headers={"X-CSRF-Token": token}).status_code == 200
            data = client.get("/api/v1/capabilities").json()["data"]
            for feature in ("writing", "diagram", "testExtraction"):
                assert data["ai"][feature] == {"available": available, "reason": reason}
                assert ai_status(feature) == data["ai"][feature]
                if available:
                    require_ai(feature)
                else:
                    with pytest.raises(AppError) as raised:
                        require_ai(feature)
                    assert raised.value.code == "AI_UNAVAILABLE"
                    assert "AI_BASE_URL" not in raised.value.message
            assert ("adminHint" in data["ai"]) is admin
            assert data["wordPreview"]["available"] is False and data["visio"]["available"] is False


def test_capabilities_require_login():
    migrate()
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/capabilities").status_code == 401


@pytest.mark.parametrize("failed", [False, True])
def test_office_cache_and_com_release_order(monkeypatch, failed):
    events = []

    class Application:
        def Quit(self):
            events.append("quit")
            if failed:
                raise RuntimeError("Office unavailable")

        def __del__(self):
            events.append("release")

    def dispatch(name):
        events.append(name)
        return Application()

    com = SimpleNamespace(CoInitialize=lambda: events.append("init"), CoUninitialize=lambda: events.append("uninit"))
    client = SimpleNamespace(DispatchEx=dispatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    monkeypatch.setitem(sys.modules, "win32com", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    _office.cache_clear()
    try:
        for name in ("Word", "Visio", "Word", "Visio"):
            assert _office(name)["available"] is not failed
        assert events == ["init", "Word.Application", "quit", "release", "uninit",
                          "init", "Visio.Application", "quit", "release", "uninit"]
    finally:
        _office.cache_clear()
