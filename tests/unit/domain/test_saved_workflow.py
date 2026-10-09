"""Domain tests for saved-workflow definitions.

The definition is the one piece of a workflow that arrives as untrusted JSON and
is then executed later, so validation is the security-relevant part of this
feature: a definition that slips through is a run that fails — or does something
the user did not save — long after they could connect the two. These tests pin
what is rejected, not just what is accepted.
"""

import pytest

from src.domain.workflows.entities.saved_workflow import (
    InvalidWorkflowError,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_OPERATIONS,
    OPERATION_CONVERT,
    SOURCE_SELECT_AT_RUN,
    SavedWorkflow,
    WorkflowDefinition,
    WorkflowOperation,
    parse_definition,
    validate_description,
    validate_name,
)


def convert(target: str = "pdf") -> dict:
    return {"type": "convert", "target_format": target}


class TestParseDefinition:
    def test_accepts_a_single_convert_operation(self):
        definition = parse_definition({"operations": [convert("pdf")]})
        assert definition.source == SOURCE_SELECT_AT_RUN
        assert len(definition.operations) == 1
        assert definition.operations[0].type == OPERATION_CONVERT
        assert definition.operations[0].target_format == "pdf"

    def test_defaults_the_source_when_absent(self):
        # Stated explicitly in the parsed form even when omitted in the raw
        # JSON, so a stored definition is self-describing.
        assert parse_definition({"operations": [convert()]}).source == SOURCE_SELECT_AT_RUN

    def test_normalises_the_target_format(self):
        # The conversion endpoint lowercases and strips a leading dot, so a
        # workflow must agree with it about what "PDF" means.
        for raw in ("PDF", " .Pdf ", ".pdf"):
            assert parse_definition({"operations": [convert(raw)]}).operations[0].target_format == "pdf"

    def test_keeps_operation_order(self):
        definition = parse_definition(
            {"operations": [convert("pdf"), convert("docx"), convert("pdf")]}
        )
        assert [op.target_format for op in definition.operations] == ["pdf", "docx", "pdf"]

    def test_rejects_a_definition_that_is_not_an_object(self):
        for raw in (None, [], "convert", 7):
            with pytest.raises(InvalidWorkflowError):
                parse_definition(raw)  # type: ignore[arg-type]

    def test_rejects_an_empty_operation_list(self):
        for raw in ([], ()):
            with pytest.raises(InvalidWorkflowError, match="at least one"):
                parse_definition({"operations": raw})

    def test_rejects_a_missing_operation_list(self):
        with pytest.raises(InvalidWorkflowError, match="at least one"):
            parse_definition({})

    def test_rejects_an_unknown_operation_type(self):
        # The closed set is what stops a definition from smuggling in a step the
        # runner would then have to interpret.
        with pytest.raises(InvalidWorkflowError, match="unsupported type"):
            parse_definition({"operations": [{"type": "delete_everything", "target_format": "pdf"}]})

    def test_rejects_an_operation_without_a_type(self):
        with pytest.raises(InvalidWorkflowError, match="unsupported type"):
            parse_definition({"operations": [{"target_format": "pdf"}]})

    def test_rejects_an_operation_that_is_not_an_object(self):
        with pytest.raises(InvalidWorkflowError, match="must be an object"):
            parse_definition({"operations": ["pdf"]})

    def test_rejects_a_non_string_target_format(self):
        for raw in (None, 7, {"a": 1}, ["pdf"]):
            with pytest.raises(InvalidWorkflowError, match="target_format"):
                parse_definition({"operations": [{"type": "convert", "target_format": raw}]})

    def test_rejects_an_empty_target_format(self):
        for raw in ("", "   ", "."):
            with pytest.raises(InvalidWorkflowError, match="empty target_format"):
                parse_definition({"operations": [{"type": "convert", "target_format": raw}]})

    def test_rejects_an_overlong_target_format(self):
        with pytest.raises(InvalidWorkflowError, match="longer than"):
            parse_definition({"operations": [convert("x" * 50)]})

    def test_rejects_too_many_operations(self):
        too_many = [convert("pdf")] * (MAX_OPERATIONS + 1)
        with pytest.raises(InvalidWorkflowError, match="at most"):
            parse_definition({"operations": too_many})

    def test_rejects_an_unsupported_source(self):
        # Only "select_at_run" exists. A stored source naming something else
        # must not be quietly reinterpreted as the default.
        with pytest.raises(InvalidWorkflowError, match="Unsupported workflow source"):
            parse_definition({"source": "some_folder", "operations": [convert()]})

    def test_reports_which_operation_is_invalid(self):
        with pytest.raises(InvalidWorkflowError, match="Operation 1"):
            parse_definition(
                {"operations": [convert("pdf"), {"type": "convert", "target_format": ""}]}
            )


class TestRoundTrip:
    def test_survives_to_dict_and_back(self):
        # Storage is JSON, so a definition that cannot round-trip is a
        # definition that changes meaning when the user reloads the page.
        original = WorkflowDefinition(
            source=SOURCE_SELECT_AT_RUN,
            operations=(
                WorkflowOperation(type=OPERATION_CONVERT, target_format="pdf"),
                WorkflowOperation(type=OPERATION_CONVERT, target_format="docx"),
            ),
        )
        assert parse_definition(original.to_dict()) == original

    def test_lists_distinct_target_formats_in_order(self):
        definition = WorkflowDefinition(
            source=SOURCE_SELECT_AT_RUN,
            operations=(
                WorkflowOperation(type=OPERATION_CONVERT, target_format="pdf"),
                WorkflowOperation(type=OPERATION_CONVERT, target_format="docx"),
                WorkflowOperation(type=OPERATION_CONVERT, target_format="pdf"),
            ),
        )
        # Used to validate every selected file before a run starts, so a
        # duplicate would mean validating the same conversion twice.
        assert definition.target_formats() == ("pdf", "docx")


class TestValidateName:
    def test_trims(self):
        assert validate_name("  Prepare application  ") == "Prepare application"

    def test_rejects_an_empty_name(self):
        for raw in (None, "", "   "):
            with pytest.raises(InvalidWorkflowError, match="name is required"):
                validate_name(raw)

    def test_rejects_an_overlong_name(self):
        with pytest.raises(InvalidWorkflowError, match="at most"):
            validate_name("x" * (MAX_NAME_LENGTH + 1))

    def test_accepts_a_name_at_the_limit(self):
        assert len(validate_name("x" * MAX_NAME_LENGTH)) == MAX_NAME_LENGTH


class TestValidateDescription:
    def test_defaults_to_empty(self):
        assert validate_description(None) == ""
        assert validate_description("") == ""

    def test_trims(self):
        assert validate_description("  why it exists  ") == "why it exists"

    def test_rejects_an_overlong_description(self):
        with pytest.raises(InvalidWorkflowError, match="at most"):
            validate_description("x" * (MAX_DESCRIPTION_LENGTH + 1))


class TestSavedWorkflow:
    def test_defaults_to_no_runs_and_no_description(self):
        workflow = SavedWorkflow(
            workflow_id="w1",
            user_id=1,
            name="Convert and archive",
            definition=parse_definition({"operations": [convert("pdf")]}),
        )
        assert workflow.description == ""
        assert workflow.run_count == 0
