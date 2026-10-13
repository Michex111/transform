"""Domain tests for an execution and its output-bounding rule."""

import pytest

from src.domain.workspaces.entities.execution import (
    EXCERPT_LIMIT_CHARS,
    MAX_SOURCE_CHARS,
    Execution,
    bound_output,
)
from src.domain.workspaces.exceptions import InvalidExecutionTransition
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus


def _execution(**overrides) -> Execution:
    values = {
        "execution_id": "ex-1",
        "workspace_id": "ws-1",
        "user_id": 7,
        "mode": ExecutionMode.SCRIPT,
        "source": "print('hi')",
    }
    values.update(overrides)
    return Execution(**values)


class TestConstruction:
    def test_a_new_execution_is_queued(self):
        assert _execution().status is ExecutionStatus.QUEUED

    @pytest.mark.parametrize("bad", ["", "   ", "\n"])
    def test_an_empty_source_is_rejected(self, bad):
        with pytest.raises(InvalidExecutionTransition):
            _execution(source=bad)

    def test_an_oversized_source_is_rejected(self):
        with pytest.raises(InvalidExecutionTransition):
            _execution(source="x" * (MAX_SOURCE_CHARS + 1))


class TestOutputBounding:
    def test_short_output_is_returned_whole(self):
        excerpt, truncated = bound_output("hello")
        assert excerpt == "hello"
        assert truncated is False

    def test_long_output_keeps_the_tail_and_says_how_much_was_dropped(self):
        text = "A" * 100 + "END"
        excerpt, truncated = bound_output(text, limit=80)
        assert truncated is True
        assert excerpt.endswith("END")
        assert "23 earlier characters omitted" in excerpt

    def test_a_tiny_limit_still_returns_output_not_just_a_notice(self):
        """A budget too small for the sentence degrades to an ellipsis.

        The alternative — a longer marker — produced an excerpt that was
        entirely explanation and contained none of the program's output.
        """
        excerpt, truncated = bound_output("A" * 100 + "END", limit=20)
        assert truncated is True
        assert excerpt.endswith("END")
        assert "omitted" not in excerpt

    def test_the_bound_is_not_dramatically_exceeded(self):
        excerpt, _ = bound_output("x" * 10_000, limit=100)
        assert len(excerpt) <= 100

    def test_a_non_positive_limit_is_a_programming_error(self):
        with pytest.raises(ValueError):
            bound_output("x", limit=0)

    def test_succeeding_bounds_both_streams(self):
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.succeed(
            exit_code=0,
            stdout="o" * (EXCERPT_LIMIT_CHARS + 500),
            stderr="e",
        )
        assert execution.stdout_truncated is True
        assert execution.stderr_truncated is False
        assert execution.stderr_excerpt == "e"


class TestLifecycle:
    def test_a_successful_run_records_exit_code_duration_and_artifacts(self):
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.succeed(exit_code=0, stdout="ok", duration_ms=1234)

        assert execution.status is ExecutionStatus.SUCCEEDED
        assert execution.exit_code == 0
        assert execution.duration_ms == 1234
        assert execution.duration_seconds() == pytest.approx(1.234)
        assert execution.completed_at is not None
        assert execution.is_terminal() is True

    def test_a_nonzero_exit_code_is_still_a_successful_execution(self):
        """``SUCCEEDED`` means "we ran it", not "the program exited 0".

        The process's own status is data the user reads; conflating it with the
        platform's status would make a deliberately failing script look like a
        platform error.
        """
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.succeed(exit_code=2, stderr="boom")
        assert execution.status is ExecutionStatus.SUCCEEDED
        assert execution.exit_code == 2

    def test_a_queued_execution_can_fail_before_it_ever_ran(self):
        execution = _execution()
        execution.fail(error="queue unavailable")
        assert execution.status is ExecutionStatus.FAILED
        assert execution.exit_code is None

    def test_a_timeout_is_distinct_from_a_failure(self):
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.time_out(duration_ms=600_000)
        assert execution.status is ExecutionStatus.TIMED_OUT

    def test_succeeding_before_starting_is_refused(self):
        with pytest.raises(InvalidExecutionTransition):
            _execution().succeed(exit_code=0)

    def test_cancelling_relabels_a_failure_less_run(self):
        execution = _execution()
        assert execution.cancel() is True
        assert execution.status is ExecutionStatus.CANCELLED
        assert execution.error == "Execution cancelled."

    def test_cancelling_an_already_finished_execution_is_a_no_op(self):
        """The client may race a completion; asking again must not be an error."""
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.succeed(exit_code=0)
        assert execution.cancel() is False
        assert execution.status is ExecutionStatus.SUCCEEDED

    def test_a_succeeded_execution_has_no_legal_transitions_left(self):
        execution = _execution()
        execution.start(runtime_id="rt-1")
        execution.succeed(exit_code=0)
        for transition in (
            lambda: execution.start(runtime_id="rt-2"),
            lambda: execution.fail(error="late"),
            lambda: execution.time_out(),
        ):
            with pytest.raises(InvalidExecutionTransition):
                transition()
