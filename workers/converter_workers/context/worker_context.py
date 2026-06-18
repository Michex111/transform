import os
from dataclasses import dataclass, field
from uuid import uuid4
from typing import Optional, Tuple

from src.domain.value_object.conversion_type import ConversionType
from ..ports import QueuePort, StoragePort
from src.infrastructure.converters.converter_registry import ConverterRegistry

@dataclass(frozen=True)
class WorkerContext:
    storage_port: StoragePort
    queue_port: QueuePort
    converter_registry: ConverterRegistry
    worker_name: str = "file_converter_worker"
    worker_id: str = field(default_factory=lambda: f"{WorkerContext.worker_name}_{uuid4()}")
    process_id: int = field(default_factory=os.getpid)


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