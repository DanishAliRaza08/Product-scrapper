"""FastAPI application entrypoint."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import analytics, auth, connections, exports, jobs, misc, products, projects
from app.core.config import get_settings
from app.core.errors import install_error_handlers

log = logging.getLogger("mpi.api")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Versioned API shared by the Windows desktop client and the cloud web dashboard.",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Path only: query strings may carry signed tokens and are never logged.
        log.info(
            "%s %s %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            extra={"request_id": request_id},
        )
        return response

    for module in (auth, jobs, products, projects, connections, exports, analytics, misc):
        app.include_router(module.router, prefix=settings.api_prefix)
    app.include_router(misc.health_router)
    return app


app = create_app()
