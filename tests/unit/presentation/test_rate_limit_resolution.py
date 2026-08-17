"""Tests for rate-limit key resolution (per-API-key, auth paths)."""


from src.presentation.api.middleware.rate_limit import RateLimitMiddleware


class _FakeRequest:
    def __init__(self, path: str, headers: dict | None = None, host: str = "1.2.3.4"):
        self.url = type("URL", (), {"path": path})()
        self.headers = headers or {}
        self.client = type("Client", (), {"host": host})()


def _middleware() -> RateLimitMiddleware:
    # Instantiate without Redis; settings come from the cached env.
    return RateLimitMiddleware.__new__(RateLimitMiddleware)  # type: ignore[no-untyped-call]


def test_api_key_requests_use_per_key_limit() -> None:
    mw = _middleware()
    mw._settings = type("S", (), {"RATE_LIMIT_API_KEY_DEFAULT": 1000})()  # type: ignore[assignment]
    req = _FakeRequest("/api/v1/credits/balance", headers={"x-api-key": "tr_abc123"})

    key, limit = mw._resolve_limit(req)  # type: ignore[arg-type]
    assert key.startswith("apikey:")
    assert limit == 1000


def test_auth_endpoints_get_stricter_limit() -> None:
    mw = _middleware()
    mw._settings = type("S", (), {"RATE_LIMIT_AUTH": 10})()  # type: ignore[assignment]
    req = _FakeRequest("/api/users/token")

    key, limit = mw._resolve_limit(req)  # type: ignore[arg-type]
    assert key.endswith(":auth")
    assert limit == 10


def test_guest_path_uses_guest_limit() -> None:
    from src.infrastructure.config.settings import get_settings

    mw = _middleware()
    mw._settings = get_settings()
    req = _FakeRequest("/api/v1/conversions/guest")

    key, limit = mw._resolve_limit(req)  # type: ignore[arg-type]
    assert key == "ip:1.2.3.4"
    assert limit == get_settings().RATE_LIMIT_GUEST
