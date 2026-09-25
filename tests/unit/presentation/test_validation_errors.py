"""Request-validation messages must be addressed to a person.

The regression this file exists to prevent: a two-character username reached the
SPA as **"String should have at least 3 characters"** — a sentence about a Python
type that never names the field the user typed into. FastAPI's default handler
serialises pydantic's error records verbatim, and those records are written for
whoever declared the schema.

The important tests here are not the hand-built inputs — they are
``test_real_schema_errors_never_leak_pydantic_vocabulary`` and its companions,
which drive the **actual** request models and assert on the messages the API
would really emit. A hand-written table of error dicts would pass while the real
schemas kept leaking, because the leak comes from pydantic's own phrasing.
"""

import json
from typing import Any

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from src.presentation.api.validation_errors import (
    message_for,
    validation_error_detail,
)
from src.presentation.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    RequestPhoneVerificationRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
    UserCreateRequest,
    VerifyEmailRequest,
    VerifyPhoneRequest,
)

#: Words that only make sense to someone reading the schema. None of them may
#: ever appear in a message shown to a user.
_IMPLEMENTATION_VOCABULARY = (
    "String",
    "Field required",
    "Input should",
    "value_error",
    "string_too_short",
    "None is not an allowed value",
    "type=value_error",
    "pydantic",
    "Python",
)


def _request_error(errors: list[dict[str, Any]]) -> RequestValidationError:
    """A ``RequestValidationError`` carrying ``errors``.

    Built directly rather than by sending a request: the handler is a pure
    function of the error records, so the mapping can be tested without an app,
    a client or a database.
    """
    return RequestValidationError(errors)


def _message_for_error(**overrides: Any) -> str:
    error: dict[str, Any] = {"type": "value_error", "loc": ("body", "field"), "msg": ""}
    error.update(overrides)
    return message_for(error)


# ---------------------------------------------------------------------------
# The reported defect
# ---------------------------------------------------------------------------


def test_a_short_username_is_told_about_the_username() -> None:
    """The exact case from the bug report."""
    with pytest.raises(ValidationError) as caught:
        UserCreateRequest.model_validate(
            {"username": "ab", "email": "ada@example.com", "password": "Sup3rSecret"}
        )

    detail = validation_error_detail(_request_error(list(caught.value.errors())))

    assert len(detail) == 1
    assert detail[0]["msg"] == "Username must be at least 3 characters."
    assert "String" not in detail[0]["msg"]


def test_a_short_username_names_the_field_in_a_full_response() -> None:
    """End-to-end through the handler, including the ``{"detail": [...]}`` shape."""
    with pytest.raises(ValidationError) as caught:
        UserCreateRequest.model_validate(
            {"username": "a", "email": "ada@example.com", "password": "Sup3rSecret"}
        )

    exc = _request_error(list(caught.value.errors()))
    body = {"detail": validation_error_detail(exc)}

    assert body["detail"][0]["msg"] == "Username must be at least 3 characters."
    # `loc` is what lets the SPA put the message under the input it names, so it
    # must survive unchanged. This path validates a model directly, so the path
    # starts at the field; FastAPI prefixes it with `body` for a real request,
    # which `test_email_verification_flow.py` pins on the wire.
    assert body["detail"][0]["loc"] == ["username"]


# ---------------------------------------------------------------------------
# The general case: every real schema, every real message
# ---------------------------------------------------------------------------


def _invalid_payloads() -> list[tuple[str, type[BaseModel], dict[str, Any]]]:
    """``(label, model, invalid payload)`` — one per user-facing field rule."""
    return [
        ("username too short", UserCreateRequest, {"username": "ab", "email": "a@b.co", "password": "Sup3rSecret"}),
        ("username too long", UserCreateRequest, {"username": "u" * 51, "email": "a@b.co", "password": "Sup3rSecret"}),
        ("username missing", UserCreateRequest, {"email": "a@b.co", "password": "Sup3rSecret"}),
        ("email missing", UserCreateRequest, {"username": "ada", "password": "Sup3rSecret"}),
        ("password too short", UserCreateRequest, {"username": "ada", "email": "a@b.co", "password": "short"}),
        ("password too long", UserCreateRequest, {"username": "ada", "email": "a@b.co", "password": "p" * 129}),
        ("password not a string", UserCreateRequest, {"username": "ada", "email": "a@b.co", "password": 12345}),
        ("name too long", UserCreateRequest, {"username": "ada", "email": "a@b.co", "password": "Sup3rSecret", "first_name": "n" * 51}),
        ("nothing at all", UserCreateRequest, {}),
        ("reset token missing", ResetPasswordRequest, {"new_password": "Sup3rSecret"}),
        ("new password too short", ResetPasswordRequest, {"token": "t", "new_password": "short"}),
        ("forgot email missing", ForgotPasswordRequest, {}),
        ("resend email missing", ResendVerificationRequest, {}),
        ("verify token missing", VerifyEmailRequest, {}),
        ("phone number too short", RequestPhoneVerificationRequest, {"phone_number": "123"}),
        ("phone code too short", VerifyPhoneRequest, {"code": "1"}),
        ("current password missing", ChangePasswordRequest, {"new_password": "Sup3rSecret"}),
        ("profile last name too long", UpdateProfileRequest, {"last_name": "n" * 51}),
    ]


@pytest.mark.parametrize(
    ("label", "model", "payload"),
    _invalid_payloads(),
    ids=[row[0] for row in _invalid_payloads()],
)
def test_real_schema_errors_never_leak_pydantic_vocabulary(
    label: str, model: type[BaseModel], payload: dict[str, Any]
) -> None:
    """Every message the API would emit must be free of schema-speak.

    Drives the real models, so it cannot drift from what pydantic actually
    produces — the whole point, since the leak is pydantic's own wording rather
    than anything this codebase writes.
    """
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)

    detail = validation_error_detail(_request_error(list(caught.value.errors())))

    assert detail, f"{label}: a validation failure produced no message"
    for entry in detail:
        message = entry["msg"]
        assert message, f"{label}: empty message"
        # Sentences, capitalised and terminated.
        assert message[0].isupper(), f"{label}: {message!r} is not capitalised"
        assert message.endswith("."), f"{label}: {message!r} is not a sentence"
        for word in _IMPLEMENTATION_VOCABULARY:
            assert word not in message, f"{label}: {word!r} leaked into {message!r}"


@pytest.mark.parametrize(
    ("label", "model", "payload"),
    _invalid_payloads(),
    ids=[row[0] for row in _invalid_payloads()],
)
def test_every_message_names_a_field(
    label: str, model: type[BaseModel], payload: dict[str, Any]
) -> None:
    """A message the user cannot act on is barely better than the old one.

    Either it names the field ("Username is required.") or it explains the whole
    request ("The request body is not valid JSON."). A bare "is not valid." with
    no subject is what this rules out.
    """
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)

    detail = validation_error_detail(_request_error(list(caught.value.errors())))

    for entry in detail:
        message = entry["msg"]
        # The field name is the last string in `loc`; its prettified form must
        # appear in the message.
        last = [part for part in entry["loc"] if isinstance(part, str) and part != "body"]
        if last:
            expected = last[-1].replace("_", " ")
            assert expected[0].upper() + expected[1:] in message, (
                f"{label}: {message!r} does not name {expected!r}"
            )


def test_a_password_is_never_echoed_back() -> None:
    """The old payload carried the submitted value in ``input``.

    For a password field that means the response body can hold part of a
    credential, which then lands in a client's error state, a log aggregator or a
    pasted bug report. Nothing consumed the key, so it is gone.
    """
    secret = "s3cr3t-that-must-not-be-echoed-" + "x" * 120  # over max_length
    with pytest.raises(ValidationError) as caught:
        UserCreateRequest.model_validate(
            {"username": "ada", "email": "a@b.co", "password": secret}
        )

    exc = _request_error(list(caught.value.errors()))
    detail = validation_error_detail(exc)

    assert secret not in json.dumps(detail)
    assert "input" not in detail[0]
    assert "ctx" not in detail[0]


def test_the_response_body_is_plain_json() -> None:
    """No ``jsonable_encoder`` needed, and it can be logged verbatim.

    ``ctx`` holds raw constraint values and, for a ``value_error``, the raised
    exception object — neither is JSON-serialisable, and both were previously in
    the payload.
    """
    with pytest.raises(ValidationError) as caught:
        UserCreateRequest.model_validate({"username": "ab", "email": "a@b.co", "password": "x"})

    body = {"detail": validation_error_detail(_request_error(list(caught.value.errors())))}

    # The default encoder raises on anything non-primitive.
    assert json.loads(json.dumps(body)) == body


# ---------------------------------------------------------------------------
# Message mapping
# ---------------------------------------------------------------------------


def test_field_names_become_form_labels() -> None:
    """Snake-case is a Python convention the user never sees."""
    detail = validation_error_detail(
        _request_error([{"type": "missing", "loc": ("body", "new_password"), "msg": ""}])
    )

    assert detail[0]["msg"] == "New password is required."


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (("body", "username"), "Username"),
        (("body", "first_name"), "First name"),
        (("body", "phone_number"), "Phone number"),
        # A list item names the collection, not the index: "Items 0" reads as a bug.
        (("body", "items", 0), "Items"),
        (("body", "items", 0, "count"), "Count"),
        # No field to name — the body itself is malformed.
        (("body",), "Request"),
        ((), "Request"),
    ],
)
def test_the_label_names_the_field_a_user_can_see(loc: tuple[Any, ...], expected: str) -> None:
    assert _message_for_error(type="missing", loc=loc).startswith(f"{expected} ")


def test_a_minimum_of_one_is_required_not_a_character_count() -> None:
    """"At least 1 characters" is the kind of detail that reads as machine output."""
    message = _message_for_error(
        type="string_too_short", loc=("body", "password"), ctx={"min_length": 1}
    )

    assert message == "Password is required."


def test_the_character_count_is_pluralised() -> None:
    assert "at least 3 characters" in _message_for_error(
        type="string_too_short", ctx={"min_length": 3}
    )
    # A one-character minimum never reaches here (see above), but the bound must
    # not produce "1 characters" if a future constraint is singular.
    assert "at most 1 character." in _message_for_error(
        type="string_too_long", ctx={"max_length": 1}
    )


def test_a_validator_message_is_used_verbatim() -> None:
    """A message the team wrote is already addressed to a user and knows its field.

    Prefixing the label would double up ("Username: Username is already taken"),
    and pydantic prepends "Value error, " which is pure implementation.
    """
    message = _message_for_error(
        type="value_error", loc=("body", "username"), msg="Value error, Username is already taken"
    )

    assert message == "Username is already taken"


def test_an_empty_validator_message_still_names_the_field() -> None:
    message = _message_for_error(type="value_error", msg="Value error, ")

    assert message == "Field is not valid."


def test_an_unknown_error_type_degrades_instead_of_leaking() -> None:
    """A pydantic upgrade must not turn a 422 into a 500 or a novel vocabulary.

    Raising inside an error handler would replace the caller's explanation with a
    500, so the mapping is total.
    """
    message = _message_for_error(type="some_future_error_type", loc=("body", "username"))

    assert message == "Username is not valid."


def test_malformed_error_records_do_not_raise() -> None:
    """``loc``/``ctx``/``type`` missing or the wrong type must not crash."""
    detail = validation_error_detail(_request_error([{}, {"loc": None, "type": None, "msg": None}]))

    assert len(detail) == 2
    assert all(entry["msg"] for entry in detail)
    # A record with no location is still reported, against the request itself.
    assert detail[0]["loc"] == []


def test_an_empty_error_list_still_explains_something() -> None:
    """A 422 with no reason is worse than a generic one."""
    detail = validation_error_detail(_request_error([]))

    assert len(detail) == 1
    assert detail[0]["msg"] == "The request is not valid."
    assert detail[0]["loc"] == ["body"]


def test_several_errors_are_all_reported() -> None:
    """A form submitting three bad fields must learn about all three at once."""
    with pytest.raises(ValidationError) as caught:
        UserCreateRequest.model_validate({"username": "ab", "password": "short"})

    detail = validation_error_detail(_request_error(list(caught.value.errors())))
    messages = [entry["msg"] for entry in detail]

    assert "Username must be at least 3 characters." in messages
    assert "Password must be at least 8 characters." in messages
    assert "Email is required." in messages
