"""Inert stand-ins for the assistant's document-side collaborators.

WHY this exists: the assistant's tool box takes eight collaborators, and a test
that is about one of them (the workspace tools, say) still has to supply the
other seven. Hand-rolling those inside each test file produced stubs that did not
satisfy the ports they stood in for — which the type checker caught, and which
would have quietly weakened the test if it had not.

Every method here raises ``AssertionError``. That is the point: these objects
exist so a workspace test can construct a *real* ``AssistantToolBox``, and if a
document tool ever runs in such a test the failure is loud and immediate rather
than a silent ``None`` flowing into an assertion.

They are deliberately complete (every member the ports declare) so that adding a
method to a port is a type error here rather than a runtime surprise.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserFileModel, UserFolderModel

_NOT_UNDER_TEST = "the document side of the tool box is not under test here"


class InertFileService:
    """Stands in for ``FileService`` (``AssistantFileServicePort``)."""

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        raise AssertionError(_NOT_UNDER_TEST)

    async def list_files(
        self,
        user_id: int,
        folder_id: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        raise AssertionError(_NOT_UNDER_TEST)

    async def list_all_files(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        raise AssertionError(_NOT_UNDER_TEST)

    async def search_files(
        self,
        user_id: int,
        query: str,
        *,
        offset: int = 0,
        limit: int = 50,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        raise AssertionError(_NOT_UNDER_TEST)

    async def create_folder(
        self, user_id: int, name: str, parent_id: str | None = None
    ) -> UserFolderModel:
        raise AssertionError(_NOT_UNDER_TEST)

    async def move_file(
        self, user_id: int, file_id: str, folder_id: str | None
    ) -> UserFileModel:
        raise AssertionError(_NOT_UNDER_TEST)

    async def delete_file(self, user_id: int, file_id: str) -> None:
        raise AssertionError(_NOT_UNDER_TEST)


class InertConversionService:
    """Stands in for ``ConversionService`` (``AssistantConversionServicePort``)."""

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier | None = None,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> ConversionJob:
        raise AssertionError(_NOT_UNDER_TEST)

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        raise AssertionError(_NOT_UNDER_TEST)

    async def list_history(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        since: datetime | None = None,
    ) -> tuple[list[ConversionJob], int]:
        raise AssertionError(_NOT_UNDER_TEST)

    async def search_jobs(
        self,
        user_id: int,
        *,
        query: str | None = None,
        fmt: str | None = None,
        limit: int = 10,
    ) -> list[ConversionJob]:
        raise AssertionError(_NOT_UNDER_TEST)


class InertFolderRepository:
    """Stands in for the folder lookup port."""

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None:
        return None

    async def list_by_parent(
        self, user_id: int, parent_id: str | None, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[UserFolderModel], int]:
        return [], 0


class InertStorage:
    """Stands in for the object-storage slice (``AssistantDocumentStorage``)."""

    async def stat_object(self, object_key: str) -> dict[str, Any] | None:
        return None

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        return b""


class InertExtractor:
    """Stands in for the document text extractor."""

    def extract(self, *, file_name: str, data: bytes) -> Any:
        raise AssertionError(_NOT_UNDER_TEST)


class InertAccountPort:
    """Stands in for the account overview port."""

    async def overview(
        self,
        user_id: int,
        *,
        since: Any = None,
        fmt: str | None = None,
        status: str | None = None,
    ) -> Any:
        raise AssertionError(_NOT_UNDER_TEST)


class InertModels:
    """Stands in for the tier→model resolver."""

    def for_tier(self, tier: SubscriptionTier) -> Any:
        raise AssertionError(_NOT_UNDER_TEST)
