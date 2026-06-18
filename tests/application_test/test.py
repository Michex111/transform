import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.domain.entities.conversion_job import ConversionJob
from src.domain.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.storage.minio_storage import get_storage
from src.infrastructure.redis.client import create_redis_client
from tests.application_test.dependencies import get_redis_queue
from src.application.services.conversion_service import ConversionService
from src.infrastructure.converters.converter_registry import get_registry

import asyncio

first_job = ConversionJob(
    job_id="2",
    conversion=ConversionType(
        "docx",
        "pdf"    
    ),
    input_file="s3-file_store/first_test.docx",
)



service = ConversionService(queue_port=get_redis_queue(create_redis_client()))

async def main():
    registry = get_registry()
    print(registry.list_conversions())
    await service.submit_conversion_job(first_job)


if __name__ == "__main__":
    asyncio.run(main())