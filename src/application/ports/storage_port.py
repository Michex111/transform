from src.application.ports.contracts import JobStoragePort, StorageGateway

# Backward-compatible alias for existing imports.
StoragePort = JobStoragePort
    def save_job(self, job: ConversionJob) -> None:...

