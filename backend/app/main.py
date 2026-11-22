"""FastAPI application: startup, middleware and error handlers.

Run locally:  uvicorn app.main:app --reload   (from the backend/ folder)
Docs:         http://localhost:8000/docs
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, chat, documents, system
from app.api.deps import RateLimiter
from app.config import Settings, get_settings
from app.db.models import RequestMetric
from app.db.session import create_schema, make_engine, make_sessionmaker
from app.errors import AppError, AuthenticationError
from app.observability.logging import configure_logging, request_id_var
from app.services.container import build_container

log = logging.getLogger("app")


def _error(
    status: int, code: str, message: str, details: dict | None = None, headers: dict | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get(), "details": details or {}}}
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings (fake providers, SQLite, in-memory Qdrant)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        engine = make_engine(settings.database_url)
        await create_schema(engine)
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.container = build_container(settings)
        app.state.rate_limiter = RateLimiter()
        await app.state.container.store.ensure_collection()
        log.info("started", extra={"provider": app.state.container.mode})
        yield
        await engine.dispose()

    app = FastAPI(
        title="Production RAG Service",
        version="1.0.0",
        description="Upload documents and ask questions answered only from them, with citations.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        """Give every request an id, time it and store one metrics row."""
        rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        token = request_id_var.set(rid[:40])
        start, status, error_type = time.perf_counter(), 500, None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id_var.get()
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            latency = int((time.perf_counter() - start) * 1000)
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            m = getattr(request.state, "metric", {}) or {}  # per-step trace set by /chat/ask
            trace = {k: v for k, v in m.items() if k.endswith("_ms") and v is not None} or None
            log.info(
                "request",
                extra={"method": request.method, "path": path, "status": status, "latency_ms": latency, "trace": trace},
            )
            if request.method != "OPTIONS" and hasattr(request.app.state, "sessionmaker"):
                try:
                    async with request.app.state.sessionmaker() as db:
                        db.add(
                            RequestMetric(
                                request_id=request_id_var.get(),
                                user_id=getattr(request.state, "user_id", None),
                                method=request.method,
                                route=path,
                                status_code=status,
                                latency_ms=latency,
                                error_type=error_type or getattr(request.state, "error_code", None),
                                **m,
                            )
                        )
                        await db.commit()
                except Exception:  # metrics must never break a request
                    log.warning("could not store request metric")
            request_id_var.reset(token)

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        request.state.error_code = exc.code
        if exc.status_code >= 500:
            log.warning(exc.message, extra={"error_type": exc.code})
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        if exc.status_code == 429 and "retry_after" in exc.details:
            headers = {"Retry-After": str(exc.details["retry_after"])}
        return _error(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state.error_code = "VALIDATION_ERROR"
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return _error(422, "VALIDATION_ERROR", "The request is invalid.", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "Something went wrong. Quote the request id when reporting it.")

    for module in (auth, documents, chat, system):
        app.include_router(module.router)
    return app


app = create_app()
