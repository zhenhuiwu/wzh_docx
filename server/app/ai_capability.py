"""AI 与本机 Office 能力（阶段2 S1，01 §4.8）：AI 是否可用的唯一判断来源。"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request

from app.config import get_settings
from app.errors import AppError
from app.identity import require_session

router = APIRouter()

Feature = Literal["writing", "diagram", "testExtraction"]
_LOCAL = {"localhost", "127.0.0.1", "::1"}
_ADMIN_HINT = "AI_BASE_URL、AI_MODEL、AI_API_KEY"
_FEATURE_TEXT = {"writing": "AI 写作", "diagram": "AI 图表", "testExtraction": "AI 测试项提取"}


def ai_status(feature: Feature, settings=None) -> dict[str, object]:
    """只检查配置，不探测连通性。三个功能目前规则相同：远程地址必须 HTTPS 且配置 API Key。

    settings 由调用方传入自己模块里取到的配置（便于测试替换），缺省读取当前配置。"""
    del feature
    settings = settings or get_settings()
    base, model = settings.ai_base_url.strip().rstrip("/"), settings.ai_model.strip()
    if not base or not model:
        return {"available": False, "reason": "not_configured"}
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return {"available": False, "reason": "invalid_url"}
    if parsed.hostname not in _LOCAL and (parsed.scheme != "https" or not settings.ai_api_key):
        return {"available": False, "reason": "remote_requires_https_key"}
    return {"available": True, "reason": None}


def require_ai(feature: Feature, settings=None) -> None:
    """不可用时 503 AI_UNAVAILABLE；文案面向用户，不出现配置项名称（02 P1-5）。"""
    status = ai_status(feature, settings)
    if not status["available"]:
        raise AppError(503, "AI_UNAVAILABLE", f"{_FEATURE_TEXT[feature]}暂不可用，请联系系统管理员配置 AI 服务。",
                       details={"reason": status["reason"]})


@lru_cache(maxsize=1)
def _office(app_name: str) -> dict[str, object]:
    """进程启动后检测一次 Word/Visio COM 能否创建。"""
    if sys.platform != "win32":
        return {"available": False, "reason": f"{app_name.lower()}_not_installed"}
    try:  # pragma: no cover - Windows only
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            application = win32com.client.DispatchEx(f"{app_name}.Application")
            application.Quit()
        finally:
            pythoncom.CoUninitialize()
        return {"available": True, "reason": None}
    except Exception:  # pragma: no cover - Windows only
        return {"available": False, "reason": f"{app_name.lower()}_not_installed"}


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, object]:
    actor = require_session(request)
    ai: dict[str, object] = {feature: ai_status(feature) for feature in ("writing", "diagram", "testExtraction")}
    if "system_admin" in actor.roles:
        ai["adminHint"] = _ADMIN_HINT
    return {
        "data": {"ai": ai, "wordPreview": _office("Word"), "visio": _office("Visio")},
        "requestId": request.state.request_id,
    }
