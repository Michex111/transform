from infrastructure.redis.client import create_redis_client
from domain.entities.conversion_job import ConversionJob
from domain.value_object.conversion_type import ConversionType
from .messages import ConversionJobMessage as JobMessage

from redis.asyncio import Redis
from redis.exceptions import ResponseError



class RedisStreamQueue:
    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.stream_name = "conversion_jobs"

class JobStream(RedisStreamQueue):
    """Implements a Redis Stream for conversion jobs. Used by the producer to push new jobs into the stream."""

    async def push_job(self, job: ConversionJob) -> None:
        message: dict = JobMessage.from_conversion_job(job).to_dict()
        await self.redis_client.xadd(self.stream_name, message)

class JobStreamConsumer(RedisStreamQueue):
    """Implements a Redis Stream consumer for conversion jobs. Used by the worker to fetch jobs from the stream."""
    
    def __init__(self, consumer_group: str, consumer_name: str, redis_client: Redis):
        super().__init__(redis_client)
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name

    @classmethod
    async def create(cls, consumer_group: str, consumer_name: str, redis_client: Redis) -> 'JobStreamConsumer':
        stream = cls(
            consumer_group,
            consumer_name,
            redis_client
        )
        await stream._ensure_consumer_group()
        return stream

    async def _ensure_consumer_group(self):
        try:
            await self.redis_client.xgroup_create(self.stream_name, self.consumer_group, id='0', mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                pass  # Consumer group already exists
            else:
                raise

    async def fetch_job(self) -> tuple[str, ConversionJob] | None:
        jobs = await self.redis_client.xreadgroup(
            self.consumer_group, 
            self.consumer_name, 
            {self.stream_name: '>'}, 
            count=1, block=10000
        )

        if not jobs:
            return None
        
        message_id, job = extract_job(jobs)
        conversation_job = ConversionJob(
            job_id=job["job_id"],
            conversion=ConversionType(source_format=job["source_format"], target_format=job["target_format"]),
            input_file=job["input_key"],
            output_file="" # Implement this,
        )

        return message_id, conversation_job
    
    async def acknowledge_job(self, message_id: str):
        await self.redis_client.xack(
            self.stream_name,
            self.consumer_name,
            message_id
        )

    async def fail_job(self, message_id: str, error_message: str):
        ...
   
def extract_job(job) -> tuple:       
    _, messages = job[0]

    message_id, data = messages[0]

    return message_id, data
