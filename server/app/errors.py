"""Error envelope shared by every response that fails."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass
class AppError(Exception):
    """A contract error. The message must not contain secrets or paths."""

    status_code: int
    code: str
    message: str
    field_errors: list[dict[str, str]] = field(default_factory=list)
    current_revision: int | None = None
    # Seconds until the client may retry; sent as the Retry-After header (429/503).
    retry_after: int | None = None


def request_id_of(request: Request) -> str:
    value = getattr(request.state, "request_id", "")
    return value or "unknown"


def error_content(request: Request, error: AppError) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": error.code,
        "message": error.message,
    }
    if error.field_errors:
        payload["fieldErrors"] = error.field_errors
    if error.current_revision is not None:
        payload["currentRevision"] = error.current_revision
    return {"error": payload, "requestId": request_id_of(request)}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_content(request, exc),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        wrapped = AppError(
            422,
            "VALIDATION_FAILED",
            "请求字段不正确。",
        )
        return JSONResponse(
            status_code=wrapped.status_code,
            content=error_content(request, wrapped),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        del exc
        wrapped = AppError(
            500,
            "INTERNAL_ERROR",
            "服务暂时不能完成这个请求。",
        )
        return JSONResponse(
            status_code=wrapped.status_code,
            content=error_content(request, wrapped),
        )
