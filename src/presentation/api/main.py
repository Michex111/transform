"""
Transform - File Conversion API

A production-ready file conversion backend service with
real-time progress updates, subscription-based billing,
and secure file storage.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.initializer import initialize_database
from src.infrastructure.database.session import get_engine
from src.infrastructure.adapters.storage.cors import apply_bucket_cors
from src.presentation.api.middleware.rate_limit import build_rate_limit_middleware
from src.presentation.api.middleware.security_headers import SecurityHeadersMiddleware
from src.presentation.api.routers.v1 import (
    api_keys,
    conversions,
    credits,
    dashboard,
    events,
    files,
    guest,
    subscriptions,
    upload,
    users,
    webhooks,
)

logger = logging.getLogger(__name__)

# Fail fast on insecure production configuration (weak SECRET_KEY, wildcard
# CORS with credentials, plaintext object storage).
settings = get_settings()

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.RUN_MIGRATIONS:
        await initialize_database()
        logger.info("Database migrations applied")
    else:
        logger.info("Skipping database migrations (RUN_MIGRATIONS=false)")

    # Announce how verification email is being delivered. `Settings.validate()`
    # already logs an ERROR when no transport is configured; this line names the
    # resolved transport on every boot so "are we actually sending?" is
    # answerable from the log alone, instead of by registering a test account.
    logger.warning(
        "Email transport: %s (verification gate %s)",
        settings._resolve_email_backend(),
        "enforced"
        if settings.EMAIL_VERIFICATION_REQUIRED
        and settings._resolve_email_backend() != "console"
        else "SUSPENDED — see Settings.validate()",
    )

    # Ensure the object-storage bucket allows browser uploads from the SPA
    # origin(s). Failures are non-fatal (logged) but configured origins are
    # honoured so direct uploads are not blocked by bucket CORS.
    try:
        await asyncio.to_thread(apply_bucket_cors)
    except Exception as e:  # noqa: BLE001 — startup must proceed
        logger.warning("Bucket CORS setup failed: %s", e)

    yield


app = FastAPI(
    title="Transform - File Converter API",
    description="Convert files between formats with real-time progress tracking",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (Redis-backed with in-memory fallback)
app.add_middleware(build_rate_limit_middleware)

# Security hardening headers
app.add_middleware(SecurityHeadersMiddleware)


def _metric_path(request: Request) -> str:
    """Prometheus ``path`` label for a request.

    Uses the matched route *template* (e.g. ``/api/v1/files/{file_id}``) rather
    than the raw path. Using the raw path made every scanner/404 path a new
    time series (unbounded memory and ``/metrics`` growth) and leaked
    job/file ids into the metrics. Requests that matched no route share the
    single ``"unmatched"`` label.
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    path = _metric_path(request)
    HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
    HTTP_REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness check — the process is up and serving traffic."""
    return {"status": "ok"}


@app.get("/ready", tags=["health"], response_model=None)
async def readiness_check() -> JSONResponse:
    """Readiness check — verifies the database is reachable."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ready", "database": "ok"})
    except Exception as e:
        logger.error("Readiness check failed: %s", e)
        return JSONResponse(
            content={"status": "not_ready", "database": "unavailable"},
            status_code=503,
        )


@app.get("/metrics", tags=["health"], include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Include all routers v1 routers
app.include_router(users.router)
app.include_router(upload.router)
app.include_router(conversions.router)
app.include_router(guest.router)
app.include_router(files.router)
app.include_router(credits.router)
app.include_router(subscriptions.router)
app.include_router(api_keys.router)
app.include_router(webhooks.router)
app.include_router(dashboard.router)
app.include_router(events.router)


# ---------------------------------------------------------------------------
# Frontend: served separately
# ---------------------------------------------------------------------------
# The React SPA is *not* served by the API. It is built and hosted as a
# standalone static site (Render static site `transform-web`) which talks to
# this API cross-origin. Keeping the API free of SPA/static-file concerns means
# the API image no longer needs a Node toolchain, and any unknown path correctly
# returns a JSON 404 instead of an HTML application shell.
#
# The browser is expected to reach this API cross-origin, so `ALLOWED_ORIGINS`
# (and the object-storage bucket CORS origin list) must include the SPA origin.


if __name__ == "__main__":
    uvicorn.run("src.presentation.api.main:app", host="0.0.0.0", port=8000, reload=True)
    