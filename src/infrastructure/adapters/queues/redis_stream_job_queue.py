from collections import deque

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from .messages import ConversionJobMessage as JobMessage

from redis.asyncio import Redis
from redis.exceptions import ResponseError
import asyncio
from src.infrastructure.logging.loggers import worker_logger


# Maximum number of dead-letter entries retained. The dead-letter stream has no
# consumer (it exists for inspection/replay), so without a bound it grows with
# every failed job. 10k entries is far more than an operator needs to triage
# and keeps memory flat on a capped Redis plan.
_DEAD_LETTER_MAXLEN = 10_000


class RedisStreamQueue:
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.stream_name = "conversion_jobs"

class JobStream(RedisStreamQueue):
    """Implements a Redis Stream for conversion jobs. Used by the producer to push new jobs into the stream."""

    async def publish_job(self, job: ConversionJob, stream: str | None = None) -> None:
        message: dict = JobMessage.from_conversion_job(job).to_dict()
        # Deliberately unbounded: an approximate MAXLEN on the *input* streams
        # can silently evict a job that has not been consumed yet, which is
        # exactly the message-loss class this adapter must not introduce. The
        # stream only grows while publishes outpace consumption.
        await self.redis_client.xadd(stream or self.stream_name, message)

class JobStreamConsumer(RedisStreamQueue):
    """Implements a Redis Stream consumer for conversion jobs. Used by the worker to fetch jobs from the stream."""

    # Tier streams consumed by workers, in priority order (high first).
    STREAMS = (
        "conversion_jobs:high",
        "conversion_jobs:normal",
        "conversion_jobs:low",
        "conversion_jobs",
    )

    def __init__(self, consumer_group: str, consumer_name: str, redis_client: Redis):
        super().__init__(redis_client)
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        # Delivery key -> stream. Keyed by the *composite* key handed back by
        # ``fetch_job`` because Redis stream ids are only unique within a
        # stream: two streams can mint the same literal id in the same
        # millisecond, and keying on the id alone lets the second delivery
        # overwrite the first so the first job's ACK goes to the wrong stream
        # (silently, because XACK returns 0 for an unknown entry).
        self._message_streams: dict[str, str] = {}
        # Entries a single ``xreadgroup`` call delivered beyond the one returned
        # to the caller. Redis' COUNT is per-stream, so one read can return an
        # entry for every stream with traffic; the extras are already in this
        # consumer's PEL and `'>'` will never hand them back, so they must be
        # buffered here or they are lost. Drained (in the priority order Redis
        # returned) before the next blocking read.
        self._buffer: deque[tuple[str, str, dict]] = deque()

    def describe_endpoint(self) -> str:
        """Redacted Redis endpoint (``host:port/db``) for startup logging.

        Never includes the username or password, so it is safe to log. A
        mismatched ``REDIS_URL`` between the API and the workers is otherwise a
        silent black hole: jobs pile up in a stream nobody consumes and the
        container still looks healthy.
        """
        try:
            kwargs = getattr(self.redis_client.connection_pool, "connection_kwargs", None) or {}
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            return "unknown"
        host = kwargs.get("path") or kwargs.get("host") or "unknown"
        port = kwargs.get("port")
        endpoint = f"{host}:{port}" if port else str(host)
        db = kwargs.get("db")
        return f"{endpoint}/{db}" if db is not None else endpoint

    @classmethod
    async def create(cls, consumer_group: str, consumer_name: str, redis_client: Redis, max_retries: int = 12, base_delay: float = 5.0) -> 'JobStreamConsumer':
        """
        Create a JobStreamConsumer with retry logic.
        
        Args:
            consumer_group: Redis consumer group name
            consumer_name: Redis consumer name
            redis_client: Redis client instance
            max_retries: Maximum number of connection attempts (default: 12 = ~2 minutes)
            base_delay: Initial delay between retries in seconds (default: 5)
        """
        stream = cls(
            consumer_group,
            consumer_name,
            redis_client
        )
        await stream._ensure_consumer_group_with_retry(max_retries=max_retries, base_delay=base_delay)
        return stream

    async def _ensure_consumer_group_with_retry(self, max_retries: int = 12, base_delay: float = 5.0):
        """
        Ensure consumer group exists with exponential backoff retry logic.
        
        Args:
            max_retries: Maximum number of connection attempts
            base_delay: Initial delay between attempts in seconds
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                await self._ensure_consumer_group()
                worker_logger.info(
                    "Successfully connected to Redis consumer group '%s' at %s; streams: %s",
                    self.consumer_group,
                    self.describe_endpoint(),
                    ", ".join(self.STREAMS),
                )
                return
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    # Keep exponential backoff bounded to avoid multi-hour startup stalls.
                    delay = min(base_delay * (2 ** attempt), 60.0)
                    worker_logger.warning(
                        f"Failed to connect to Redis (attempt {attempt + 1}/{max_retries}). "
                        f"Retrying in {delay:.1f} seconds... Error: {str(e)}"
                    )
                    await asyncio.sleep(delay)
                else:
                    worker_logger.error(
                        f"Failed to connect to Redis after {max_retries} attempts. "
                        f"Please ensure Redis is running and accessible."
                    )
                    raise

    async def _ensure_consumer_group(self):
        for stream in self.STREAMS:
            try:
                await self.redis_client.xgroup_create(stream, self.consumer_group, id='0', mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" in str(e):
                    pass  # Consumer group already exists
                else:
                    raise

    async def fetch_job(self) -> tuple[str, ConversionJob] | None:
        # Serve entries buffered by an earlier read first. They were already
        # delivered into this consumer's PEL, so the priority order Redis
        # returned them in must be honoured before asking for new work.
        if self._buffer:
            return self._build_job(*self._buffer.popleft())

        response = await self.redis_client.xreadgroup(  # type: ignore[arg-type]
            self.consumer_group,
            self.consumer_name,
            {stream: '>' for stream in self.STREAMS},
            count=1,
            block=10000,
        )

        if not response:
            return None

        # Redis returns one reply element per stream that had data — COUNT is
        # per-stream, not a global budget for the call. Every entry in the
        # response has been delivered into this consumer's PEL, and `'>'` will
        # never hand it back, so anything dropped here is unreachable until the
        # idle threshold lets ``reclaim_stale_jobs`` pick it up. Drain the whole
        # response: return the first entry and buffer the rest.
        deliveries: deque[tuple[str, str, dict]] = deque()
        for stream_name, messages in response:  # type: ignore[union-attr]
            for message_id, fields in messages:  # type: ignore[union-attr]
                deliveries.append((str(stream_name), str(message_id), dict(fields)))  # type: ignore[arg-type]
        if not deliveries:
            return None

        first = deliveries.popleft()
        self._buffer.extend(deliveries)
        return self._build_job(*first)

    @staticmethod
    def _delivery_key(stream: str, message_id: str) -> str:
        """Composite key identifying a delivered entry.

        Stream ids are only unique within a stream, so the stream has to travel
        with the message for a later ACK to be unambiguous.
        """
        return f"{stream}:{message_id}"

    def _build_job(self, stream: str, message_id: str, fields: dict) -> tuple[str, ConversionJob]:
        """Reconstruct the job entity for a delivered entry.

        Returns the delivery key (not the bare message id) alongside the job —
        that key is what ``acknowledge_job``/``fail_job`` are given back, and
        it is what routes the ACK to the stream the message actually came from.
        """
        raw = dict(fields)
        job = {str(k): str(v) for k, v in raw.items()}

        delivery_key = self._delivery_key(stream, message_id)
        self._message_streams[delivery_key] = stream

        conversation_job = ConversionJob(
            job_id=str(job["job_id"]),
            conversion=ConversionType(
                source_format=str(job["source_format"]),
                target_format=str(job["target_format"]),
            ),
            input_file=str(job["input_key"]),
            object_key=str(job["object_key"]),
            output_file="",  # This will be set later when the job is completed
            status=JobStatus.PENDING,
            user_id=int(job["user_id"]) if job.get("user_id") else None,
            # Client-side (FENCR) encryption metadata. ``client_encrypted`` is a
            # "true"/"false" string in the stream; parse it back to a bool.
            client_encrypted=JobMessage._to_bool(job.get("client_encrypted")),
            data_key_wrapped=str(job["data_key_wrapped"]) if job.get("data_key_wrapped") else None,
        )

        return delivery_key, conversation_job

    def _stream_for_message(self, message_id: str) -> str:
        """Stream a delivered message came from, keyed by its delivery key."""
        stream = self._message_streams.pop(message_id, None)
        if stream is not None:
            return stream

        # No recorded mapping: either the key was already resolved (a second
        # ACK) or the map was lost across a restart. Decode the delivery key
        # instead of falling back to a fixed stream, which would send the ACK
        # somewhere else. Log it so the anomaly is visible.
        decoded, _, _ = message_id.rpartition(":")
        if decoded in self.STREAMS:
            worker_logger.warning(
                "No recorded stream for delivery key %r; decoding it as %r",
                message_id, decoded,
            )
            return decoded

        worker_logger.warning(
            "Unrecognised delivery key %r; acknowledging against %r instead",
            message_id, self.stream_name,
        )
        return self.stream_name

    @staticmethod
    def _raw_message_id(stream: str, delivery_key: str) -> str:
        """Strip the ``"<stream>:"`` prefix from a delivery key."""
        prefix = f"{stream}:"
        return delivery_key[len(prefix):] if delivery_key.startswith(prefix) else delivery_key

    async def acknowledge_job(self, message_id: str):
        stream = self._stream_for_message(message_id)
        await self.redis_client.xack(
            stream,
            self.consumer_group,
            self._raw_message_id(stream, message_id),
        )

    async def fail_job(self, message_id: str, error_message: str):
        stream = self._stream_for_message(message_id)
        await self.redis_client.xack(
            stream,
            self.consumer_group,
            self._raw_message_id(stream, message_id),
        )

    async def dead_letter_job(self, message_id: str, error_message: str, job: ConversionJob):
        """Copy a failed job to the dead-letter stream for later inspection/replay.

        ``message_id`` is the delivery key returned by ``fetch_job``; it is
        stored verbatim as ``original_message_id`` so an operator can correlate
        the dead letter with the entry it came from. The stream is bounded
        because nothing consumes it.
        """
        message: dict = JobMessage.from_conversion_job(job).to_dict()
        message["error"] = error_message
        message["original_message_id"] = message_id
        await self.redis_client.xadd(
            "conversion_jobs:dead", message, maxlen=_DEAD_LETTER_MAXLEN
        )

    async def reclaim_stale_jobs(self, min_idle_ms: int = 60_000, count: int = 20) -> int:
        """Re-queue messages that were left pending by crashed workers.

        A message read with ``xreadgroup`` stays in the consumer group's
        ``pending`` list until it is ACKed. If a worker dies mid-job the
        message would otherwise be stuck forever: its DB row stays
        ``PROCESSING`` with no terminal event and the user's job never
        finishes. This scans each stream's pending list for entries idle longer
        than ``min_idle_ms``, ``XAUTOCLAIM``s them to this consumer, republishes
        a copy onto the same stream, and only then ACKs the claimed original —
        a true re-queue, so the normal ``fetch_job`` path delivers it.

        Re-queueing instead of holding the claimed entries in a local buffer is
        deliberate. A claimed-but-not-yet-processed entry stays in this
        consumer's PEL with an ever-growing idle time, so a peer's next sweep
        could not distinguish "crashed worker" from "worker holding a backlog"
        and would re-process it — converting and charging the user twice. A
        copy appended to the stream starts its idle clock fresh and is
        delivered normally.

        Write order matters: the copy exists before the original is ACKed, so a
        failure at either step can only duplicate a job, never lose one.

        Returns the number of messages re-queued.
        """
        requeued = 0
        for stream in self.STREAMS:
            try:
                response = await self.redis_client.xautoclaim(
                    stream,
                    self.consumer_group,
                    self.consumer_name,
                    min_idle_time=min_idle_ms,
                    start_id="0-0",
                    count=count,
                )
            except Exception as e:  # noqa: BLE001 — best-effort sweep
                worker_logger.warning(f"Failed to reclaim stale jobs from {stream}: {e}")
                continue

            # Response: [next_cursor, entries, deleted_ids] (Redis 7+)
            if not response or len(response) < 2 or not isinstance(response[1], list):
                continue

            stream_requeued = 0
            for message_id, fields in response[1]:
                if await self._requeue(stream, str(message_id), dict(fields)):
                    stream_requeued += 1

            requeued += stream_requeued
            if stream_requeued:
                worker_logger.warning(
                    "Re-queued %d stale pending job(s) from %s", stream_requeued, stream
                )
        return requeued

    async def _requeue(self, stream: str, message_id: str, fields: dict) -> bool:
        """Republish a claimed entry onto ``stream`` and ACK the original."""
        try:
            await self.redis_client.xadd(stream, fields)
        except Exception as e:  # noqa: BLE001 — leave the original pending
            # Nothing was published, so the original stays in the PEL and the
            # next sweep retries it. No loss.
            worker_logger.warning(
                "Failed to re-queue stale job %s from %s: %s", message_id, stream, e
            )
            return False
        try:
            await self.redis_client.xack(stream, self.consumer_group, message_id)
        except Exception as e:  # noqa: BLE001
            # The copy is already queued, so this can only duplicate the job,
            # never lose it. The original stays pending and will be reclaimed
            # again, so log at ERROR for triage.
            worker_logger.error(
                "Re-queued stale job %s from %s but failed to ACK the original "
                "(the job may be processed twice): %s",
                message_id, stream, e,
            )
        return True


# Number of extra messages a single ``fetch_job`` can deliver beyond the one it
# returns: Redis' COUNT is per-stream, so one call returns up to one entry per
# stream in ``JobStreamConsumer.STREAMS``. The stale-idle threshold is derived
# from this (see ``workers.converter_workers.worker.stale_min_idle_ms``): a
# buffered entry starts accruing idle time before the job ahead of it finishes,
# so the threshold has to cover that backlog or a peer could reclaim and
# re-process a message this worker is about to run.
MAX_BUFFERED_MESSAGES_PER_FETCH = len(JobStreamConsumer.STREAMS) - 1
