from src.domain.entities.conversion_job import ConversionJob
from typing import Protocol

class StorageUrlGateway(Protocol):
    """
    Protocol for object storage operations.
    """

    def generate_upload_url(self, object_key: str) -> str:
        """
        Generate a pre-signed URL for uploading an object.

        Args:
            object_key: The key of the object to be uploaded.

        Returns:
            A pre-signed URL for uploading the object.
        """
        ...

    def generate_download_url(self, object_key: str, expires_in_minutes: int) -> str:
        """
        Generate a pre-signed URL for downloading an object.

        Args:
            object_key: The key of the object to be downloaded.
            expires_in_minutes: How long the download URL should remain valid.

        Returns:
            A pre-signed URL for downloading the object.
        """
        ...

    