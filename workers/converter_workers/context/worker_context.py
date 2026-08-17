import os
from dataclasses import dataclass, field
from uuid import uuid4
from typing import Optional

from workers.converter_workers.ports import JobEventPort, JobRepositoryPort, QueuePort, StoragePort
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.converters.converter_registry import ConverterRegistry

@dataclass(frozen=True)
class WorkerContext:
    storage_port: StoragePort
    queue_port: QueuePort
    event_port: JobEventPort
    converter_registry: ConverterRegistry
    worker_name: str = "file_converter_worker"
    job_repository: JobRepositoryPort | None = None
    encryption_service: FileEncryptionService | None = None
    worker_id: str = field(init=False)
    process_id: int = field(default_factory=os.getpid)

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_id", f"{self.worker_name}_{uuid4()}")


    def get_log_context(self, job_id: Optional[str] = None, conversion_type: Optional[ConversionType] = None) -> dict:
        context = {
            "worker_id": self.worker_id,
            "worker_name": self.worker_name,
            "process_id": self.process_id
        }
        if job_id:
            context["job_id"] = job_id
        if conversion_type:
            context["conversion_type"] = f"{conversion_type.source_format}->{conversion_type.target_format}"
        return context