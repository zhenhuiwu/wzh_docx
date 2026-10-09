"""Template rendering stays closed until a Word acceptance environment exists."""

from __future__ import annotations

from app.errors import AppError


def render_acceptance_sample() -> None:
    raise AppError(
        409,
        "PRECONDITION_MISSING",
        "目标办公环境尚未确认，不能生成可发布的版式结果。",
    )
