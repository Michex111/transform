"""Records MCP tool invocations for the Developer > MCP Activity page.

This lives in the presentation layer because that is where the verified
identity is available: the MCP SDK has already validated the OAuth access token
and placed it in the request context, and ``get_access_token()`` exposes it. The
identity recorded here is therefore the *token's* subject and client — never an
argument a tool received, and never a name the agent supplied.

Why instrumentation is a decorator rather than a call inside each tool: the
record must exist for **every** tool, including the ones added later, and a
per-tool call is exactly the kind of line that gets forgotten. Wrapping the
registered function makes coverage a property of the registration, not of the
author's diligence.

Outcome classification:

* a returned ``{"ok": False, ..., "required_scope": ...}`` payload is
  ``DENIED`` — this is how the toolbox reports a scope refusal, and it is a
  different event from a malfunction. A user investigating "what did my agent
  try?" needs to see blocked attempts, so they are kept distinct rather than
  folded into errors.
* any other returned value is ``SUCCESS``.
* a raised exception is ``ERROR`` with a **coarse category**, never the
  exception text: a raw message can quote file contents or internal paths, and
  this table must never become a way to read a document.

Recording goes through the same bounded ingestion queue as API request events,
so it is fire-and-forget: a telemetry failure can never break an agent's tool
call. The failure is *logged* rather than swallowed silently — a swallowed
instrumentation error is indistinguishable from "nobody called the tools", and
that made one outage take a full trace to diagnose. The tool call still returns
normally either way.
"""

import functools
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from mcp.server.auth.middleware.auth_context import get_access_token

from src.domain.telemetry.entities.api_request_event import McpToolInvocation, ToolOutcome
from src.infrastructure.telemetry.ingestion import get_telemetry_ingestion

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

logger = logging.getLogger(__name__)

#: Exception type names that mean "the caller asked for something that is not
#: theirs or does not exist". Mapped to a category rather than stored verbatim.
_NOT_FOUND_HINTS = ("NotFound", "NoSuchKey", "FileRecordNotFound")
_PERMISSION_HINTS = ("Permission", "Forbidden", "Unauthorized", "Denied")


def _error_category(exc: BaseException) -> str:
    name = type(exc).__name__
    if any(hint in name for hint in _PERMISSION_HINTS):
        return "permission_denied"
    if any(hint in name for hint in _NOT_FOUND_HINTS):
        return "not_found"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "invalid_request"
    return "internal"


def _classify(result: Any) -> tuple[str, str | None]:
    """Map a tool's return value to an outcome."""
    if isinstance(result, dict) and result.get("ok") is False:
        if "required_scope" in result:
            return ToolOutcome.DENIED, "permission_denied"
        return ToolOutcome.ERROR, "tool_error"
    return ToolOutcome.SUCCESS, None


def _record(
    tool_name: str,
    *,
    outcome: str,
    started: float,
    error_category: str | None,
) -> None:
    """Enqueue one invocation, or silently do nothing if we cannot attribute it."""
    try:
        token = get_access_token()
        if token is None or not token.subject:
            return
        try:
            account_id = int(token.subject)
        except (TypeError, ValueError):
            return
        claims = token.claims or {}
        invocation = McpToolInvocation(
            id=str(uuid.uuid4()),
            account_id=account_id,
            grant_id=str(claims.get("grant_id", "")),
            client_id=token.client_id or "",
            tool_name=tool_name,
            outcome=outcome,
            created_at=datetime.now(UTC),
            error_category=error_category,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        get_telemetry_ingestion().record_invocation(invocation)
    except Exception:  # noqa: BLE001 — instrumentation must never break a tool call
        # The tool call itself must still succeed, but an operator debugging
        # "why is the activity page empty?" needs the reason. Only the exception
        # type and repr are logged — never tool arguments or results.
        logger.warning("MCP invocation telemetry dropped for %s", tool_name, exc_info=True)
        return


def instrument_tool(tool_name: str) -> Callable[[F], F]:
    """Wrap an MCP tool so each call is recorded against the caller's grant."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                _record(
                    tool_name,
                    outcome=ToolOutcome.ERROR,
                    started=started,
                    error_category=_error_category(exc),
                )
                raise
            outcome, category = _classify(result)
            _record(tool_name, outcome=outcome, started=started, error_category=category)
            return result

        wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        wrapper.__annotations__ = dict(getattr(func, "__annotations__", {}))
        return wrapper  # type: ignore[return-value]

    return decorator
