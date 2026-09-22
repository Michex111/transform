"""Environment namespacing for Redis stream keys.

Development and production deliberately share services (one Redis, one object
bucket), which is convenient but leaks across environments at the queue: a
single consumer group competes for every message on a stream, so a job created
in one environment can be executed by a worker bound to the other environment's
database. Two silent failures follow:

* ``SQLConversionJobRepository.update_conversion_job`` is a bare
  ``UPDATE ... WHERE job_id = ?``. Run against a database that does not hold
  the row, it affects zero rows and raises nothing, so the worker still ACKs
  the message and reports success — while the owning database's row stays
  ``PENDING`` forever and the job is gone from the queue.
* ``WorkerCreditRepository.consume(user_id, ...)`` would create or charge a
  credit bucket for that numeric ``user_id`` in the wrong database, where the
  same id is almost certainly a *different* person.

Qualifying every stream key with :data:`Settings.QUEUE_STREAM_PREFIX` keeps the
two sets of workers from consuming each other's jobs.

The default prefix is the empty string, which reproduces the original names
byte-for-byte, so production behaviour is unchanged unless the setting is set.
"""

from src.infrastructure.config.settings import get_settings

#: Base (unqualified) stream names. These are the production names.
JOB_STREAM = "conversion_jobs"
JOB_TIER_STREAMS = (
    f"{JOB_STREAM}:high",
    f"{JOB_STREAM}:normal",
    f"{JOB_STREAM}:low",
    JOB_STREAM,
)
JOB_EVENT_STREAM = "conversion_job_events"
JOB_DEAD_LETTER_STREAM = f"{JOB_STREAM}:dead"


def stream_prefix() -> str:
    """Return the configured stream namespace (``""`` when unset).

    Read through ``get_settings`` rather than ``os.getenv`` so the value is
    picked up from a ``.env`` file as well as the process environment. The
    settings object is cached, so this stays cheap.
    """
    try:
        prefix = get_settings().QUEUE_STREAM_PREFIX
    except Exception:  # noqa: BLE001 — a malformed config must not hide the queue
        return ""
    return prefix or ""


def qualify(stream_name: str) -> str:
    """Prefix ``stream_name`` with the configured namespace.

    Idempotent: an already-qualified name is returned unchanged, so a name that
    has passed through here twice is not double-prefixed.
    """
    prefix = stream_prefix()
    if not prefix or stream_name.startswith(prefix):
        return stream_name
    return f"{prefix}{stream_name}"


def qualify_all(stream_names: tuple[str, ...]) -> tuple[str, ...]:
    """Qualify every name in ``stream_names``, preserving order."""
    return tuple(qualify(name) for name in stream_names)
