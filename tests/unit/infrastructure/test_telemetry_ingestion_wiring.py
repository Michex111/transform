"""The telemetry ingestion sink must actually be wired to storage.

This exists because of a bug that was invisible by construction. The writer has
a deliberate branch for the "no storage wired" case (a unit test, or a
deliberately disabled deployment) that **counts a batch as written without
persisting it**. The production singleton was built without a
``session_factory``, so it always took that branch: every API request event and
every MCP tool invocation was counted as written and silently discarded.

The symptoms were misleading in exactly the way that matters:

* the Prometheus ``written`` counter kept rising, so "is telemetry working?"
  answered *yes*;
* ``dropped`` and ``write_failures`` stayed at ``0``, so nothing looked broken;
* the API Logs and MCP Activity pages were simply always empty — which reads as
  "nobody has used the API yet", not as "the audit trail is not recording".

So the test asserts the two things whose absence caused it: the singleton passes
a callable, and that callable returns something usable as an async context
manager yielding a session. The second half matters because the obvious fix —
passing the ``get_session_factory`` accessor itself — is wrong: it returns a
``sessionmaker``, which is not a context manager, and every flush then fails with
"does not support the asynchronous context manager protocol".
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

from src.domain.telemetry.entities.api_request_event import McpToolInvocation, ToolOutcome
from src.infrastructure.telemetry import ingestion as ingestion_module


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts and ends with no cached sink."""
    ingestion_module.set_telemetry_ingestion(None)
    yield
    ingestion_module.set_telemetry_ingestion(None)


def test_production_sink_is_wired_to_a_session_factory():
    sink = ingestion_module.get_telemetry_ingestion()
    assert sink.session_factory is not None, (
        "The ingestion sink has no session factory, so every batch is counted "
        "as written and discarded — the API Logs and MCP Activity pages will be "
        "permanently empty while the 'written' metric reports success."
    )


def test_the_session_factory_is_callable_and_yields_a_session():
    """It must be a zero-arg callable returning an async context manager.

    Guards the near-miss fix: handing over the accessor rather than its result
    fails at the first flush, not at construction, so it would only surface as a
    ``write_failures`` count in production.
    """
    sink = ingestion_module.get_telemetry_ingestion()
    factory = sink.session_factory
    assert callable(factory)

    produced = factory()
    assert hasattr(produced, "__aenter__") and hasattr(produced, "__aexit__"), (
        "session_factory() must return an async context manager; a sessionmaker "
        "is not one and will raise on the first flush."
    )
    # Close it without entering a transaction so no connection is held.
    with contextlib.suppress(Exception):
        asyncio.run(_close(produced))


async def _close(resource) -> None:
    async with resource:
        pass


def test_an_unwired_sink_still_counts_as_written():
    """The test-fixture branch is intentional — pin it so it is not 'fixed'.

    Several unit tests construct a sink with no storage and rely on the counters
    moving. Removing this branch would break them, and the real defect was the
    *production wiring*, not this behaviour.

    ``_write`` is called directly rather than through the queue so the assertion
    is about this branch and not about flush scheduling.
    """
    sink = ingestion_module.TelemetryIngestion(enabled=True)
    assert sink.session_factory is None

    invocation = McpToolInvocation(
        id="inv-wiring-1",
        account_id=1,
        grant_id="grant-wiring-1",
        client_id="client-wiring-1",
        tool_name="list_files",
        outcome=ToolOutcome.SUCCESS,
        created_at=datetime.now(UTC),
        duration_ms=1.0,
    )

    asyncio.run(sink._write([("invocation", invocation)]))

    stats = sink.stats()
    assert stats["written_invocations"] == 1, (
        "An unwired sink must still count the batch as written; unit tests rely "
        "on these counters and nothing else. If this now fails, the branch was "
        "changed — check whether production wiring was affected."
    )
    assert stats["write_failures"] == 0
