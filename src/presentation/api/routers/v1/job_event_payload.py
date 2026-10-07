"""Build one SSE ``progress`` frame's JSON payload for a conversion job.

WHY this exists as its own module: the events arrive from a Redis stream, and
Redis stores every stream field as a *string* (``xadd`` takes scalars, and the
subscriber's reader decodes them back to ``str``). So a frame that the worker
published as ``{"progress": 25}`` reaches the API as ``{"progress": "25"}``.

Forwarding that verbatim put a string into the JSON — and the SPA reads
``progress`` as a number. A string fell through its ``typeof === "number"``
guard, so `jobProgress()` returned ``null`` and the bar rendered as an
indeterminate sweep instead of tracking the job. The same leak typed every other
numeric field (``credits_used``, ``compute_duration_ms``, …) as a string on the
wire, contradicting ``JobProgressEvent``.

The SSE contract is typed, so the types are enforced here — once, for both the
authenticated and guest streams — rather than at each ``fields.get(...)``.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Fields the client reads as integers. Each key is emitted only when the frame
#: actually carried a parseable value, so "the server did not measure this"
#: stays absent instead of becoming a fabricated ``0``. ``progress`` is the
#: exception: it is always present, because every frame describes where the job
#: is, and the bar needs a number on each one.
_INT_FIELDS: tuple[str, ...] = (
    "compute_duration_ms",
    "credits_used",
    "input_size_bytes",
    "output_size_bytes",
    "credits_remaining",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is an int subclass; never a real count
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def job_event_payload(job_id: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return the typed payload for one job event frame.

    ``progress`` is always an ``int`` (``0`` when the frame carried none), and
    the optional integer fields appear only when present and parseable. A
    non-empty ``output_file`` is passed through unchanged.
    """
    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": fields.get("status", ""),
        "progress": _as_int(fields.get("progress")) or 0,
        "message": fields.get("message", ""),
    }

    for key in _INT_FIELDS:
        if key not in fields:
            continue
        parsed = _as_int(fields[key])
        if parsed is not None:
            payload[key] = parsed

    if fields.get("output_file"):
        payload["output_file"] = fields["output_file"]

    return payload
