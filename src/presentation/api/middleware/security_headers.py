"""Security hardening headers middleware."""

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


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
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for header, value in self._HEADERS.items():
            response.headers.setdefault(header, value)
        return response
