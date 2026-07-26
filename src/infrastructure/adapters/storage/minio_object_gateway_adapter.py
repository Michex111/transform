from src.infrastructure.adapters.storage.minio_storage_adapter import MinioUrlStorageAdapter


class MinioObjectGatewayAdapter:
    """Adapter exposing object download URL generation for query services."""

    def __init__(self, storage: MinioUrlStorageAdapter) -> None:
        self._storage = storage

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        """Returns a temporary URL to download a stored object."""
        return self._storage.generate_get_url(
            object_key=object_key,
            expires_in_minutes=expires_in_minutes,
        )
