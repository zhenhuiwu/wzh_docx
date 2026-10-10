"""ASGI entry. Health is open. File routes stay closed in this foundation."""

from __future__ import annotations

import logging

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.bootstrap_admin import bootstrap_first_admin, flag_known_default_passwords
from app.db import migrate, ping
from app.errors import AppError, error_content, install_error_handlers
from app.files import router as files_router
from app.identity import router as identity_router
from app.jobs import router as jobs_router
from app.knowledge import router as knowledge_router
from app.projects import router as projects_router
from app.documents import router as documents_router
from app.document_diagrams import router as document_diagrams_router
from app.document_plans import router as document_plans_router
from app.facts import router as facts_router
from app.templates import router as templates_router
from app.test_management import router as test_management_router, recover_interrupted_tasks
from app.users import router as users_router
from app.workbench import router as workbench_router
from app.writing_skills import router as writing_skills_router, seed_writing_skills
from app.models import format_timestamp

logger = logging.getLogger(__name__)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

health_router = APIRouter()


@health_router.get("/health")
def get_health(request: Request) -> dict[str, object]:
    return {
        "data": {
            "status": "ok" if ping() else "degraded",
            "timestamp": format_timestamp(datetime.now(timezone.utc)),
        },
        "requestId": request.state.request_id,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    migrate()
    seed_writing_skills()
    bootstrap_first_admin()
    flag_known_default_passwords()
    recover_interrupted_tasks()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        if _REQUEST_ID.fullmatch(incoming):
            request.state.request_id = incoming
        else:
            request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except AppError as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content=error_content(request, exc),
            )
        except StarletteHTTPException:
            raise
        except Exception:
            logger.exception("unhandled error, request %s", request.state.request_id)
            response = JSONResponse(
                status_code=500,
                content=error_content(
                    request,
                    AppError(
                        500,
                        "INTERNAL_ERROR",
                        "服务暂时不能完成这个请求。",
                    ),
                ),
            )
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    api = APIRouter(prefix="/api/v1")
    api.include_router(health_router)
    api.include_router(identity_router)
    api.include_router(users_router)
    api.include_router(projects_router)
    api.include_router(jobs_router)
    api.include_router(templates_router)
    api.include_router(writing_skills_router)
    api.include_router(documents_router)
    api.include_router(document_diagrams_router)
    api.include_router(document_plans_router)
    api.include_router(facts_router)
    api.include_router(workbench_router)
    api.include_router(knowledge_router)
    api.include_router(files_router)
    api.include_router(test_management_router)
    app.include_router(api)
    return app


app = create_app()
