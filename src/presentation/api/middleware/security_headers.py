"""Security hardening headers middleware."""

from typing import Callable
from urllib.parse import urlparse

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


def _object_store_host() -> str | None:
    """Extract the host[:port] of the configured object-storage endpoint.

    The SPA uploads files directly to object storage via presigned URLs, so the
    browser must be allowed to connect (``connect-src``) to that host. Returns
    None when the endpoint isn't a parseable URL.
    """
    from src.infrastructure.config.settings import get_settings

    endpoint = get_settings().BACKBLAZE_ENDPOINT
    if not endpoint:
        return None
    # Settings may be host[:port] (e.g. minio:9000) or a full URL.
    if "://" not in endpoint:
        return endpoint
    parsed = urlparse(endpoint)
    return parsed.netloc or None


def _build_csp() -> str:
    """Build the Content-Security-Policy, allowing the object-store origin.

    ``connect-src`` must include the object-storage host so cross-origin
    presigned uploads (PUT) from the SPA are not blocked by the browser.
    """
    connect = ["'self'"]
    host = _object_store_host()
    if host:
        connect.append(f"https://{host}")
        connect.append(f"http://{host}")

    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        f"connect-src {' '.join(connect)}; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds standard security headers to every response:

    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Referrer-Policy: no-referrer
    - X-XSS-Protection: 1; mode=block (legacy belt-and-braces)
    - Permissions-Policy: a conservative default allowlist
    - Content-Security-Policy: a SPA-friendly policy (self + inline styles for
      the built frontend; no unsafe-eval)
    - Strict-Transport-Security: enforce HTTPS for the SPA/API origin
    """

    _HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-XSS-Protection": "1; mode=block",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        # SPA safe: allow self scripts, inline styles (Tailwind/Vite), connect
        # to self and data:/blob: for file downloads. No unsafe-eval.
        # Note: the SPA uploads files directly to object storage (Backblaze B2 /
        # S3) via presigned PUT, so its host MUST be allowed in connect-src
        # (otherwise the browser blocks the upload with a "Failed to fetch" /
        # CORS error). The object-store host is derived from settings at request
        # time below so it stays in sync with config.
        "Content-Security-Policy": _build_csp(),
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for header, value in self._HEADERS.items():
            response.headers.setdefault(header, value)
        return response
