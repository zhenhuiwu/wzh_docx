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
    details: dict[str, object] | None = None
    # Seconds until the client may retry; sent as the Retry-After header (429/503).
    retry_after: int | None = None
    # A07：越过分工被拒的提交（document_id/actor/base/markdown/block_ids）；统一在错误处理里另起短事务留存（字不丢）
    rejected: dict[str, object] | None = field(default=None, repr=False)


def database_busy_error(exc: BaseException) -> AppError | None:
    """SQLite 写锁等待超过 busy_timeout → 503 DATABASE_BUSY（01 §5.4），前端按同一保存键退避重试。"""
    from app.db import is_database_busy

    if not is_database_busy(exc):
        return None
    return AppError(503, "DATABASE_BUSY", "服务器正忙，请稍后重试。", retry_after=1)


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
    if error.details is not None:
        payload["details"] = error.details
    return {"error": payload, "requestId": request_id_of(request)}


def _store_rejected(exc: AppError) -> None:
    """主事务已随请求结束回滚；这里另开连接存被拒提交。失败不影响 409 本身。"""
    try:
        from app.db import connect
        from app.document_assignments import store_rejected
        info = exc.rejected or {}
        with connect() as connection:
            store_rejected(connection, info["document_id"], info["actor"], int(info["base"]), str(info["markdown"]),
                           info.get("block_ids"), exc)
    except Exception:  # noqa: BLE001 - 留存是尽力而为，原错误照常返回
        import logging
        logging.getLogger(__name__).exception("store rejected save failed")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        if exc.rejected and not (exc.details or {}).get("rejectedSaveId"):
            _store_rejected(exc)
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
        busy = database_busy_error(exc)
        if busy is not None:
            return JSONResponse(status_code=503, content=error_content(request, busy),
                                headers={"Retry-After": str(busy.retry_after)})
        wrapped = AppError(
            500,
            "INTERNAL_ERROR",
            "服务暂时不能完成这个请求。",
        )
        return JSONResponse(
            status_code=wrapped.status_code,
            content=error_content(request, wrapped),
        )
