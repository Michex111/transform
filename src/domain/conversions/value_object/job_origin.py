from enum import StrEnum

__all__ = ["JobOrigin", "coerce_job_origin"]


class JobOrigin(StrEnum):
    """Where a conversion job was submitted from.

    Persisted domain state, not a presentation detail: the credit wallet spends
    differently depending on it. Purchased credits are reservable for programmatic
    (``API``) usage while browser sessions spend the plan allowance first, so the
    value has to survive the round-trip through the database and the queue.

    ``GUEST`` is not an authenticated actor at all — guest jobs are ownerless and
    never touch a wallet — but they are a distinct origin so that a guest job can
    never be mistaken for a signed-in browser job if ownership is ever added.

    ``MCP`` is an AI agent acting under an OAuth grant. It is separate from
    ``API`` rather than folded into it because the agent-facing
    ``get_conversion_history`` tool has to answer "what did *this agent* do?",
    and because a grant can be confined to a folder by a decision the API-key
    caller never made. Recording it as ``API`` would make both questions
    unanswerable from the row.
    """

    WEB = "WEB"
    API = "API"
    MCP = "MCP"
    GUEST = "GUEST"


def coerce_job_origin(value: object) -> JobOrigin:
    """Best-effort parse of a persisted or transported origin.

    Defaults to :attr:`JobOrigin.WEB` for anything unrecognised rather than
    raising, because the two producers that reach here can legitimately be
    *older than this feature*:

    * an old worker's job message has no ``origin`` field at all, and
    * a job entry already sitting in a Redis stream was written without one.

    Raising would turn an in-flight message into a permanently undeliverable
    job. A NULL from a row written before the column existed degrades the same
    way. ``WEB`` is the safe default: it is the pre-existing behaviour (plan
    credits first) and the least surprising for a human-driven job.
    """
    if isinstance(value, JobOrigin):
        return value
    if isinstance(value, str):
        try:
            return JobOrigin(value.strip().upper())
        except ValueError:
            return JobOrigin.WEB
    return JobOrigin.WEB
