import logging

from application.ports.object_storage_port import StorageUrlGateway
from application.ports.session_cache_port import SessionCache
from src.application.exceptions.file_transfer_exceptions import (
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from application.dtos.upload_dto import UploadResponse, UploadSession
from uuid import uuid4
from datetime import timedelta

class TransferService:
    def __init__(self, storage_port: StorageUrlGateway, cache_port: SessionCache, ttl_minutes: int = 10, logger: logging.Logger = logging.getLogger(__name__)):
        self._storage = storage_port
        self._cache = cache_port
        self._ttl = timedelta(minutes=ttl_minutes)
        self._logger = logger

    async def create_upload(self, file_extension: str, user_id: str) -> UploadResponse:
        upload_id = str(uuid4())
        object_key = self._generate_object_key("upload/" + upload_id, file_extension)

        upload_url = self._storage.generate_put_url(object_key)

        session = UploadSession(upload_id=upload_id, object_key=object_key, status="pending")
        await self._cache.set(upload_id, session.model_dump_json(), ttl=self._ttl)

        self._logger.info(f"Created upload session {upload_id} for user {user_id}")

        return UploadResponse(upload_id=upload_id, upload_url=upload_url, object_key=object_key, expires_in_minutes=int(self._ttl.total_seconds()))

    async def get_upload_session(self, upload_id: str) -> UploadSession:
        session_data = await self._cache.get(upload_id)
        if not session_data:
            self._logger.warning(f"Upload session not found for upload_id: {upload_id}")
            raise UploadSessionNotFoundError(f"Session {upload_id} invalid or expired")

        return UploadSession.model_validate_json(session_data)

    async def delete_upload_session(self, upload_id: str):
        await self._cache.delete(upload_id)
        self._logger.info(f"Upload session deleted for upload_id: {upload_id}")

    async def verify_upload_completion(self, upload_id: str) -> UploadSession:
        session = await self.get_upload_session(upload_id)
        if not await self._storage.verify_upload_completion(session.object_key):
            raise UploadVerificationError(f"Upload for session {upload_id} is not complete or failed.")

        return session

    def _generate_object_key(self, job_id: str, file_extension: str) -> str:
        """
        Creates a secure, collision-resistant object path.
        """

        secure_filename = f"{uuid4().hex}{file_extension.lstrip('.')}"
        return f"{job_id}/{secure_filename}"
