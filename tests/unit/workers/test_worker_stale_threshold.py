"""Tests for the stale-pending reclaim threshold (W-7).

A message's idle clock starts at delivery — before the job ahead of it has
finished — so a threshold below the conversion timeout lets a peer "reclaim" a
job a live worker is still running. That converts the file twice and, because
credits are consumed after a successful upload, charges the user twice.
"""

import workers.converter_workers.worker as worker_module
from src.infrastructure.adapters.queues.redis_stream_job_queue import (
    MAX_BUFFERED_MESSAGES_PER_FETCH,
)
from workers.converter_workers.worker import stale_min_idle_ms


def test_threshold_is_never_below_the_default_conversion_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(worker_module.settings, "WORKER_CONVERSION_TIMEOUT", 600)

    assert stale_min_idle_ms() > 600 * 1000


def test_threshold_tracks_an_overridden_conversion_timeout(monkeypatch) -> None:
    monkeypatch.setattr(worker_module.settings, "WORKER_CONVERSION_TIMEOUT", 1800)

    assert stale_min_idle_ms() > 1800 * 1000


def test_threshold_covers_the_buffered_backlog(monkeypatch) -> None:
    """One read can deliver entries for several streams.

    Those buffered entries are already delivered (so already accruing idle
    time) while the job ahead of them runs, so the threshold has to cover them
    as well — otherwise a peer reclaims a message this worker is about to run.
    """
    monkeypatch.setattr(worker_module.settings, "WORKER_CONVERSION_TIMEOUT", 600)

    one_job_of_slack = 600 * 1000
    assert MAX_BUFFERED_MESSAGES_PER_FETCH >= 1
    assert stale_min_idle_ms() > one_job_of_slack * (1 + MAX_BUFFERED_MESSAGES_PER_FETCH)


def test_threshold_replaces_the_old_five_minute_constant() -> None:
    """The previous hardcoded 5 minute threshold was below the 10 minute
    conversion timeout, which is what made the double-charge window live."""
    assert not hasattr(worker_module, "_STALE_MIN_IDLE_MS")
