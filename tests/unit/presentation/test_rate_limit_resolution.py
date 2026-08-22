"""Tests for rate-limit key resolution (per-API-key, auth paths)."""


from src.presentation.api.middleware.rate_limit import RateLimitMiddleware


class _FakeRequest:
    def __init__(self, path: str, headers: dict | None = None, host: str = "1.2.3.4"):
        self.url = type("URL", (), {"path": path})()
        self.headers = headers or {}
        self.client = type("Client", (), {"host": host})()


def _middleware() -> RateLimitMiddleware:
    # Instantiate without Redis; settings come from the cached env.
    mw = RateLimitMiddleware.__new__(RateLimitMiddleware)  # type: ignore[no-untyped-call]
    mw._redis_limiter = None  # type: ignore[assignment]
    mw._store = {}  # type: ignore[assignment]
    mw._redis_unavailable = False  # type: ignore[assignment]
    return mw


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


def test_authenticated_user_gets_higher_limit_keyed_by_token() -> None:
    mw = _middleware()
    mw._settings = type("S", (), {"RATE_LIMIT_AUTHENTICATED": 600})()  # type: ignore[assignment]
    req = _FakeRequest(
        "/api/v1/credits/balance", headers={"authorization": "Bearer abc.def.ghi"}
    )

    key, limit = mw._resolve_limit(req)  # type: ignore[arg-type]
    assert key.startswith("user:")
    assert key != "user:abc.def.ghi"  # token is hashed, not stored raw
    assert limit == 600


def test_different_bearer_tokens_map_to_different_keys() -> None:
    mw = _middleware()
    mw._settings = type("S", (), {"RATE_LIMIT_AUTHENTICATED": 600})()  # type: ignore[assignment]

    key_a, _ = mw._resolve_limit(  # type: ignore[arg-type]
        _FakeRequest("/api/v1/files", headers={"authorization": "Bearer token-a"}) #type: ignore[arg-type]
    )
    key_b, _ = mw._resolve_limit(  # type: ignore[arg-type]
        _FakeRequest("/api/v1/files", headers={"authorization": "Bearer token-b"})  #type: ignore[arg-type]
    )
    assert key_a != key_b


def test_api_key_takes_priority_over_bearer_token() -> None:
    mw = _middleware()
    mw._settings = type(  # type: ignore[assignment]
        "S", (), {"RATE_LIMIT_API_KEY_DEFAULT": 1000, "RATE_LIMIT_AUTHENTICATED": 600}
    )()
    req = _FakeRequest(
        "/api/v1/credits/balance",
        headers={"x-api-key": "tr_abc", "authorization": "Bearer token"},
    )

    key, limit = mw._resolve_limit(req)  # type: ignore[arg-type]
    assert key.startswith("apikey:")
    assert limit == 1000


def test_dispatch_skips_non_api_paths() -> None:
    """The SPA (index.html, /assets/*) must not consume the API rate budget."""
    import asyncio

    from src.infrastructure.config.settings import get_settings

    mw = _middleware()
    mw._settings = get_settings()

    async def call_next(request):  # type: ignore[no-untyped-def]
        del request
        return "ok"

    for path in ("/", "/assets/index-hash.js", "/dashboard", "/health"):
        req = _FakeRequest(path)
        result = asyncio.run(mw.dispatch(req, call_next))  # type: ignore[arg-type]
        assert result == "ok", f"{path} should not be rate-limited"


def test_dispatch_applies_to_api_paths() -> None:
    import asyncio

    mw = _middleware()
    # Force a limit of 1 so the single request is consumed and a second is blocked.
    mw._settings = type("S", (), {"RATE_LIMIT_FREE": 1, "RATE_LIMIT_AUTH": 1})() # type: ignore[assignment]

    req = _FakeRequest("/api/conversions/supported")

    async def call_next(request):  # type: ignore[no-untyped-def]
        del request
        return "ok"

    async def _run() -> tuple[str, str]:
        first = await mw.dispatch(req, call_next)  # type: ignore[arg-type]
        second = await mw.dispatch(req, call_next)  # type: ignore[arg-type]
        return first, second    # type: ignore[no-untyped-return]

    first, second = asyncio.run(_run())
    assert first == "ok"
    # Second request within the window should be blocked with a 429.
    assert getattr(second, "status_code", None) == 429
