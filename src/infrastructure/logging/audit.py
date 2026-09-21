"""Security audit logging for ISO 27001:2022 evidence.

ISO 27001:2022 Annex A.8.15 (logging) and A.8.16 (monitoring activities)
require that security-relevant events are logged in a tamper-evident,
structured way so they can be reviewed as audit evidence. This module exposes
a dedicated :data:`audit_logger` that writes structured, JSON-safe records
separate from the application's operational logs.

The logger is intentionally minimal: it emits one JSON line per security
event with a stable schema so downstream SIEM/log collectors can parse it.
It never logs secrets, tokens, passwords, or file contents.
"""

import json
import logging
import sys
import time
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

# Context variables to correlate a request/job with its security events.
_CORRELATION_ID: ContextVar[str | None] = ContextVar("audit_correlation_id", default=None)
_ACTOR: ContextVar[str | None] = ContextVar("audit_actor", default=None)


class AuditFormatter(logging.Formatter):
    """Format log records as single-line JSON with a stable event schema."""

    # Standard LogRecord attributes. None of these belong in an audit event:
    # they are implementation detail of the logging call, not evidence. Anything
    # absent from this set is treated as a deliberate structured field (either
    # ``extra=`` on the call or the mapping the helpers pass as the message's
    # arguments).
    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        payload: dict[str, Any] = {
            "ts": int(time.time() * 1000),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
        }
        # The helpers in this module pass their structured fields as the
        # message's mapping argument (logging's ``args``), which lives on
        # ``record.args`` rather than ``record.__dict__``. Promote it so the
        # documented fields stay at the top level of the event, where a SIEM
        # parses them (docs/security/access-control-policy.md lists
        # ``scope``/``key``/``limit`` and ``user_id``/``action``/``resource``).
        args = record.args
        if isinstance(args, Mapping):
            for key, value in args.items():
                payload[str(key)] = value
        # Merge structured `extra` fields, excluding reserved LogRecord keys.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value

        correlation = _CORRELATION_ID.get()
        if correlation:
            payload["correlation_id"] = correlation
        actor = _ACTOR.get()
        if actor:
            payload["actor"] = actor

        return json.dumps(payload, default=str)


def _build_audit_logger() -> logging.Logger:
    logger = logging.getLogger("transform.security_audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers on module reload (uvicorn --reload).
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(AuditFormatter())
        logger.addHandler(handler)
    return logger


audit_logger = _build_audit_logger()


def set_audit_context(*, correlation_id: str | None = None, actor: str | None = None) -> None:
    """Attach correlation/actor context to the current async context."""
    if correlation_id is not None:
        _CORRELATION_ID.set(correlation_id)
    if actor is not None:
        _ACTOR.set(actor)


def clear_audit_context() -> None:
    """Clear the per-request audit context."""
    _CORRELATION_ID.set(None)
    _ACTOR.set(None)


# Convenience helpers so call sites read cleanly and stay DRY.
def log_auth_failure(reason: str, **fields: Any) -> None:
    audit_logger.warning("auth_failure", {**fields, "reason": reason})


def log_auth_success(user_id: str, method: str, **fields: Any) -> None:
    audit_logger.info("auth_success", {**fields, "user_id": user_id, "method": method})


def log_rate_limited(scope: str, key: str, limit: int, **fields: Any) -> None:
    audit_logger.warning("rate_limited", {**fields, "scope": scope, "key": key, "limit": limit})


def log_webhook_failure(provider: str, **fields: Any) -> None:
    audit_logger.warning("webhook_failure", {**fields, "provider": provider})


def log_permission_denied(resource: str, **fields: Any) -> None:
    audit_logger.warning("permission_denied", {**fields, "resource": resource})


def log_data_access(user_id: str, action: str, resource: str, **fields: Any) -> None:
    audit_logger.info("data_access", {**fields, "user_id": user_id, "action": action, "resource": resource})
