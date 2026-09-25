"""Human-readable messages for request-validation (422) failures.

**The defect this module exists to fix.** FastAPI answers a bad request body
with pydantic's own error records, and those messages are written for the person
who declared the schema, not for the person who filled in the form. Submitting a
two-character username produced::

    {"detail": [{"loc": ["body", "username"], "msg": "String should have at least 3 characters", ...}]}

and the SPA renders ``msg`` verbatim, so a user saw the sentence **"String
should have at least 3 characters"** — a statement about a Python type, with the
field they typed into never named once. The same held for every other field
("Field required" for a missing email, "Input should be a valid integer", …).
This is the classic leak of an implementation detail into a user-facing string,
and it made an otherwise finished form read as a prototype.

**Why the fix lives here and not in the client.** The field name, the constraint
and the human label for it are all knowledge the *server* owns — it is the
server that decided a username needs three characters. Reconstructing proper
copy in the SPA would mean maintaining a second copy of every request schema,
and any other API consumer (a CLI, a mobile build, a partner integration) would
still receive "String should have at least 3 characters". One message table at
the boundary fixes every client at once.

**The response shape is deliberately unchanged.** The body is still
``{"detail": [{"loc": …, "type": …, "msg": …}, …]}`` — FastAPI's documented
``HTTPValidationError``. ``msg`` means "the message", and it now carries one a
human can read, so the generated OpenAPI stays truthful and no existing
consumer breaks. Status stays 422, because the request *was* well-formed JSON
that failed validation.

**``input`` and ``ctx`` are dropped on purpose.** They are the two keys that
pydantic always populates and that nothing consumes:

* ``input`` **echoes the submitted value back**. On a password field that means
  the response body can contain part of a credential — submitting a
  over-long password returned the password itself in ``input``. A validation
  error is the wrong place to be carrying secrets into a client's state, a log
  aggregator or a bug report.
* ``ctx`` carries raw constraint values and, for ``value_error``, the raised
  exception object. Keeping it would mean the response can only be serialised
  through ``jsonable_encoder`` and can never be logged verbatim.

Dropping both also means the payload is plain JSON-safe primitives, so this
module needs no encoder and its output can be asserted on directly in tests.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: Message per pydantic error ``type``. The bounds come from ``ctx`` where the
#: constraint has one, so the wording always states the rule that was broken
#: rather than a vague "is invalid".
_TOO_SHORT = "string_too_short"
_TOO_LONG = "string_too_long"

#: Types that mean "the value was not there at all". Grouped because they share
#: one sentence and there is no useful distinction to draw for the reader.
_MISSING_TYPES = frozenset({"missing", "string_type"})

#: Types that mean "the value is the wrong kind of number/date/boolean".
_NUMBER_TYPES = frozenset({"int_type", "int_parsing", "int_from_float", "float_type", "float_parsing"})
_WHOLE_NUMBER_TYPES = frozenset({"int_type", "int_parsing", "int_from_float"})
_DATE_TYPES = frozenset({"date_type", "date_parsing", "date_from_datetime_parsing"})
_DATETIME_TYPES = frozenset(
    {"datetime_type", "datetime_parsing", "datetime_from_date_parsing"}
)
_BOOL_TYPES = frozenset({"bool_type", "bool_parsing"})
#: Types where pydantic lists the permitted values — "not a valid option" is the
#: honest summary without dumping the enum into a toast.
_OPTION_TYPES = frozenset({"enum", "literal_error"})


def _prettify(field: str) -> str:
    """Turn a schema field name into a form label ("first_name" → "First name").

    Snake-case is a Python convention the user never sees, and title-casing
    every word ("First Name") disagrees with the labels the SPA renders
    ("Confirm new password", "Current password").
    """
    cleaned = field.replace("_", " ").strip()
    if not cleaned:
        return "This field"
    return cleaned[0].upper() + cleaned[1:]


def _field_label(loc: Sequence[Any]) -> str:
    """The label for the field an error is about.

    ``loc`` is pydantic's location path, e.g. ``("body", "username")``. The
    leading ``"body"`` is stripped — it names the request part, not a field, and
    "Body username is required" reads as nonsense. A trailing integer means the
    error is about an item in a list (``("body", "items", 0)``); naming the index
    ("Items 0") reads like a bug, so the collection's name is used instead.

    There is deliberately **no override table**. A rename would break the
    property every caller depends on — that the message names the field it is
    about — and the general rule already produces the right label for every
    field the API has (``username`` → "Username", ``new_password`` → "New
    password", ``phone_number`` → "Phone number"). A test asserts this holds for
    every request model.
    """
    parts = [part for part in loc if part != "body"]
    if not parts:
        # A malformed *body* rather than a field — e.g. invalid JSON. There is
        # no field to name.
        return "Request"

    if isinstance(parts[-1], int):
        parts = parts[:-1]
        if not parts:
            return "Item"

    return _prettify(str(parts[-1]))


def _count(value: Any, unit: str) -> str:
    """``3`` + ``"character"`` → ``"3 characters"``.

    Guards the singular: "at least 1 characters" is the kind of detail that
    makes an error read as machine-generated even when the rest is fine.
    """
    if not isinstance(value, int):
        return unit
    return f"{value} {unit}{'' if value == 1 else 's'}"


def message_for(error: Mapping[str, Any]) -> str:
    """Render one pydantic error record as a sentence naming its field.

    Pure and total: an unrecognised ``type`` still produces a usable sentence,
    so a pydantic upgrade that adds error types degrades to "Username is not
    valid." rather than leaking the new vocabulary or raising inside an error
    handler (which would turn a 422 into a 500).
    """
    kind = str(error.get("type") or "")
    label = _field_label(error.get("loc") or ())
    ctx = error.get("ctx") or {}
    if not isinstance(ctx, Mapping):  # pragma: no cover - defensive
        ctx = {}

    if kind in _MISSING_TYPES:
        return f"{label} is required."

    if kind == _TOO_SHORT:
        minimum = ctx.get("min_length")
        # A minimum of one is just "required", and saying "at least 1
        # characters" for it would be worse than saying nothing.
        if not isinstance(minimum, int) or minimum <= 1:
            return f"{label} is required."
        return f"{label} must be at least {_count(minimum, 'character')}."

    if kind == _TOO_LONG:
        return f"{label} must be at most {_count(ctx.get('max_length'), 'character')}."

    if kind in _WHOLE_NUMBER_TYPES:
        return f"{label} must be a whole number."
    if kind in _NUMBER_TYPES:
        return f"{label} must be a number."

    if kind in _DATETIME_TYPES:
        return f"{label} must be a valid date and time."
    if kind in _DATE_TYPES:
        return f"{label} must be a valid date."

    if kind in _BOOL_TYPES:
        return f"{label} must be true or false."

    if kind in _OPTION_TYPES:
        return f"{label} is not a valid option."

    if kind == "url_parsing":
        return f"{label} must be a valid URL."

    if kind == "json_invalid":
        return "The request body is not valid JSON."

    if kind in {"too_short", "too_long", "list_type"}:
        return f"{label} is not a valid list."

    if kind in {"greater_than", "greater_than_equal", "less_than", "less_than_equal"}:
        limit = ctx.get("gt") or ctx.get("ge") or ctx.get("lt") or ctx.get("le")
        return f"{label} is out of the allowed range (limit {limit})."

    if kind == "value_error":
        # A message from a validator the team wrote, so it is already addressed
        # to a user and knows its own field. Used verbatim rather than prefixed
        # with the label, which would double up ("Username: Username is taken").
        # Pydantic prepends "Value error, " to whatever was raised.
        raw = str(error.get("msg") or "")
        return raw.removeprefix("Value error, ").strip() or f"{label} is not valid."

    # Unknown type. Naming the field is still the important part; the specific
    # rule is what we cannot describe.
    return f"{label} is not valid."


def validation_error_detail(exc: Any) -> list[dict[str, Any]]:
    """Build the ``detail`` array for a :class:`RequestValidationError`.

    Returns ``[{"loc": [...], "type": ..., "msg": ...}, ...]`` — FastAPI's
    documented error-item keys, so a client can still attribute a message to a
    field via ``loc``, but with ``msg`` addressed to a person and the
    value-echoing ``input``/``ctx`` keys removed (see the module docstring).

    ``loc`` and ``type`` are passed through unchanged because they are
    machine-readable: the SPA keys off ``loc`` to place a message under the
    field it belongs to, and that only works if the location is exact.
    """
    errors: Sequence[Mapping[str, Any]] = getattr(exc, "errors", lambda: [])() or []
    detail = [
        {
            "loc": list(error.get("loc") or ()),
            "type": str(error.get("type") or "value_error"),
            "msg": message_for(error),
        }
        for error in errors
    ]

    if not detail:
        # Unreachable through FastAPI (a RequestValidationError always carries
        # at least one record), but an empty array would be a body no client can
        # explain, and a 422 with no reason is worse than a generic one.
        detail.append({"loc": ["body"], "type": "value_error", "msg": "The request is not valid."})

    return detail
