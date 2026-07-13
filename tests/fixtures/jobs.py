from collections.abc import Callable

import pytest

from src.domain.entities.conversion_job import ConversionJob
from src.domain.value_object.conversion_type import ConversionType


@pytest.fixture
def conversion_type() -> ConversionType:
    return ConversionType(source_format="txt", target_format="md")


@pytest.fixture
def conversion_job(conversion_type: ConversionType) -> ConversionJob:
    return ConversionJob(
        job_id="job-1",
        conversion=conversion_type,
        input_file="s3-file_store/input.txt",
    )


@pytest.fixture
def conversion_job_factory() -> Callable[..., ConversionJob]:
    def _create(
        job_id: str = "job-1",
        source_format: str = "txt",
        target_format: str = "md",
        input_file: str = "s3-file_store/input.txt",
    ) -> ConversionJob:
        return ConversionJob(
            job_id=job_id,
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=input_file,
        )

    return _create