"""Domain tests for the runtime instance state machine.

The runtime lifecycle is the part of the feature most likely to be driven
concurrently (a start racing a stop, a provider failing mid-start), so the
legal and illegal transitions are pinned exhaustively here.
"""

import pytest

from src.domain.workspaces.entities.workspace_runtime import WorkspaceRuntime
from src.domain.workspaces.exceptions import InvalidRuntimeTransition
from src.domain.workspaces.value_object.runtime_state import RuntimeState


def _runtime(**overrides) -> WorkspaceRuntime:
    values = {"runtime_id": "rt-1", "workspace_id": "ws-1", "provider": "docker"}
    values.update(overrides)
    return WorkspaceRuntime(**values)


class TestHappyPath:
    def test_a_full_start_and_stop_cycle(self):
        runtime = _runtime()
        runtime.begin_start()
        assert runtime.state is RuntimeState.STARTING
        assert runtime.is_serving() is False

        runtime.mark_running(provider_ref="container-abc")
        assert runtime.state is RuntimeState.RUNNING
        assert runtime.is_serving() is True
        assert runtime.provider_ref == "container-abc"
        assert runtime.started_at is not None

        runtime.begin_stop()
        assert runtime.state is RuntimeState.STOPPING

        runtime.mark_stopped()
        assert runtime.state is RuntimeState.STOPPED
        assert runtime.stopped_at is not None

    def test_a_restored_runtime_records_the_image_revision_it_came_from(self):
        runtime = _runtime()
        runtime.begin_start()
        runtime.mark_running(provider_ref="container-abc", image_revision_id="rev-9")
        assert runtime.image_revision_id == "rev-9"

    def test_a_start_clears_a_previous_failure(self):
        runtime = _runtime(state=RuntimeState.FAILED, failure_reason="daemon gone")
        runtime.begin_start()
        assert runtime.failure_reason is None

    def test_stopping_keeps_the_provider_handle(self):
        """A stopped container still exists — that is what makes restart cheap.

        Clearing the handle here would silently turn every stop/start into a
        recreate, discarding the environment the user installed. A handle that has
        really gone away is detected by the provider, not guessed at here.
        """
        runtime = _runtime()
        runtime.begin_start()
        runtime.mark_running(provider_ref="container-abc")
        runtime.begin_stop()
        runtime.mark_stopped()
        assert runtime.provider_ref == "container-abc"


class TestIllegalTransitions:
    def test_starting_an_already_running_runtime_is_refused(self):
        """The check that stops a duplicate container being allocated."""
        runtime = _runtime()
        runtime.begin_start()
        runtime.mark_running(provider_ref="c1")
        with pytest.raises(InvalidRuntimeTransition):
            runtime.begin_start()

    def test_starting_a_runtime_mid_start_is_refused(self):
        runtime = _runtime()
        runtime.begin_start()
        with pytest.raises(InvalidRuntimeTransition):
            runtime.begin_start()

    def test_running_a_runtime_that_was_never_started_is_refused(self):
        with pytest.raises(InvalidRuntimeTransition):
            _runtime().mark_running(provider_ref="c1")

    def test_a_start_may_be_abandoned(self):
        """Cancelling a slow start is a legitimate user action."""
        runtime = _runtime()
        runtime.begin_start()
        runtime.begin_stop()
        assert runtime.state is RuntimeState.STOPPING

    def test_stopping_a_stopped_runtime_is_refused(self):
        with pytest.raises(InvalidRuntimeTransition):
            _runtime().begin_stop()

    def test_marking_stopped_is_reachable_from_a_failed_runtime(self):
        """A failed runtime whose provider resources are gone is stopped."""
        runtime = _runtime(state=RuntimeState.FAILED, failure_reason="boom")
        runtime.mark_stopped()
        assert runtime.state is RuntimeState.STOPPED


class TestFailure:
    @pytest.mark.parametrize(
        "start_state",
        [RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING],
    )
    def test_failure_is_reachable_from_every_non_terminal_state(self, start_state):
        runtime = _runtime(state=start_state)
        runtime.mark_failed("provider unavailable")
        assert runtime.state is RuntimeState.FAILED
        assert runtime.failure_reason == "provider unavailable"

    def test_reporting_failure_twice_is_idempotent(self):
        """A retry loop re-reporting the same provider error must not raise."""
        runtime = _runtime(state=RuntimeState.FAILED, failure_reason="first")
        runtime.mark_failed("second")
        assert runtime.failure_reason == "second"

    def test_a_failed_runtime_can_be_restarted(self):
        runtime = _runtime(state=RuntimeState.FAILED, failure_reason="boom")
        runtime.begin_start()
        assert runtime.state is RuntimeState.STARTING


class TestQueries:
    @pytest.mark.parametrize(
        "state,serving",
        [
            (RuntimeState.STOPPED, False),
            (RuntimeState.STARTING, False),
            (RuntimeState.RUNNING, True),
            (RuntimeState.STOPPING, False),
            (RuntimeState.FAILED, False),
        ],
    )
    def test_only_a_running_runtime_is_serving(self, state, serving):
        assert _runtime(state=state).is_serving() is serving

    @pytest.mark.parametrize(
        "state,needs_start",
        [
            (RuntimeState.STOPPED, True),
            (RuntimeState.FAILED, True),
            (RuntimeState.STARTING, False),
            (RuntimeState.RUNNING, False),
        ],
    )
    def test_needs_provider_start(self, state, needs_start):
        assert _runtime(state=state).needs_provider_start() is needs_start
