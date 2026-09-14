from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from .messages import ConversionJobMessage as JobMessage

from redis.asyncio import Redis
from redis.exceptions import ResponseError
import asyncio
from src.infrastructure.logging.loggers import worker_logger



class RedisStreamQueue:
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.stream_name = "conversion_jobs"

class JobStream(RedisStreamQueue):
    """Implements a Redis Stream for conversion jobs. Used by the producer to push new jobs into the stream."""

    async def publish_job(self, job: ConversionJob, stream: str | None = None) -> None:
        message: dict = JobMessage.from_conversion_job(job).to_dict()
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
        self._message_streams: dict[str, str] = {}

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
                worker_logger.info(f"Successfully connected to Redis consumer group '{self.consumer_group}'")
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
        response = await self.redis_client.xreadgroup(  # type: ignore[arg-type]
            self.consumer_group,
            self.consumer_name,
            {stream: '>' for stream in self.STREAMS},
            count=1,
            block=10000,
        )

        if not response:
            return None

        stream_name, messages = response[0]  # type: ignore[index]
        message_id, fields = messages[0]  # type: ignore[index]
        raw = dict(fields)  # type: ignore[arg-type]
        job = {str(k): str(v) for k, v in raw.items()}

        self._message_streams[str(message_id)] = str(stream_name)

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

        return str(message_id), conversation_job
    def _stream_for_message(self, message_id: str) -> str:
        return self._message_streams.pop(message_id, "conversion_jobs")

    async def acknowledge_job(self, message_id: str):
        await self.redis_client.xack(
            self._stream_for_message(message_id),
            self.consumer_group,
            message_id,
        )

    async def fail_job(self, message_id: str, error_message: str):
        await self.redis_client.xack(
            self._stream_for_message(message_id),
            self.consumer_group,
            message_id,
        )

    async def dead_letter_job(self, message_id: str, error_message: str, job: ConversionJob):
        """Copy a failed job to the dead-letter stream for later inspection/replay."""
        message: dict = JobMessage.from_conversion_job(job).to_dict()
        message["error"] = error_message
        message["original_message_id"] = message_id
        await self.redis_client.xadd("conversion_jobs:dead", message)

    async def reclaim_stale_jobs(self, min_idle_ms: int = 60_000, count: int = 20) -> int:
        """Reclaim messages that were left pending by crashed workers.

        A message read with ``xreadgroup`` stays in the consumer group's
        ``pending`` list until it is ACKed. If a worker dies mid-job the
        message would otherwise be stuck forever. This scans each stream's
        pending list for entries idle longer than ``min_idle_ms`` and uses
        ``XAUTOCLAIM`` to transfer them to this consumer so ``fetch_job`` can
        re-process them.

        Returns the number of messages reclaimed across all streams.
        """
        reclaimed = 0
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
                # Response: [next_cursor, entries, deleted_ids] (Redis 7+)
                if response and len(response) > 1 and response[1] is not None:
                    claimed = response[1]
                    if isinstance(claimed, list):
                        for message_id, fields in claimed:
                            self._message_streams[str(message_id)] = stream
                            reclaimed += 1
                        if claimed:
                            worker_logger.warning(
                                "Reclaimed %d stale pending job(s) from %s", len(claimed), stream
                            )
            except Exception as e:  # noqa: BLE001 — best-effort sweep
                worker_logger.warning(f"Failed to reclaim stale jobs from {stream}: {e}")
        return reclaimed
