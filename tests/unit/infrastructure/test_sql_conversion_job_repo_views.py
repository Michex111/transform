import asyncio
from datetime import UTC, datetime

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _FakeSession:
    def __init__(self, execute_results: list[_ScalarResult]) -> None:
        self._execute_results = execute_results
        self.added: list[ConversionJob] = []

    async def execute(self, stmt):
        del stmt
        return self._execute_results.pop(0)

    def add(self, model):
        self.added.append(model)

    async def commit(self):
        return None


class _Row:
    def __init__(
        self,
        job_id: str,
        status: JobStatus,
        source_format: str,
        target_format: str,
        input_file: str,
        output_file: str | None,
        created_at: datetime,
        updated_at: datetime,
        error_message: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.source_format = source_format
        self.target_format = target_format
        self.input_file = input_file
        self.output_file = output_file
        self.created_at = created_at
        self.updated_at = updated_at
        self.error_message = error_message


def test_sql_conversion_job_repository_lists_history_and_active_jobs() -> None:
    async def run() -> None:
        now = datetime(2026, 7, 25, tzinfo=UTC)
        history_row = _Row(
            job_id="job-completed",
            status=JobStatus.COMPLETED,
            source_format="docx",
            target_format="pdf",
            input_file="uploads/a.docx",
            output_file="outputs/a.pdf",
            created_at=now,
            updated_at=now,
        )
        active_row = _Row(
            job_id="job-pending",
            status=JobStatus.PENDING,
            source_format="pdf",
            target_format="docx",
            input_file="uploads/b.pdf",
            output_file=None,
            created_at=now,
            updated_at=now,
        )
        session = _FakeSession(
            execute_results=[
                _ScalarResult(1),  # history total
                _ScalarResult([history_row]),  # history rows
                _ScalarResult(1),  # active total
                _ScalarResult([active_row]),  # active rows
            ]
        )
        repo = SQLConversionJobRepository(session)  # type: ignore[arg-type]

        history, history_total = await repo.list_user_history(user_id="1", offset=0, limit=20)
        active, active_total = await repo.list_user_active_jobs(user_id="1", offset=0, limit=20)

        assert history_total == 1
        assert history[0].job_id == "job-completed"
        assert history[0].status == "COMPLETED"
        assert active_total == 1
        assert active[0].job_id == "job-pending"
        assert active[0].status == "PENDING"

    asyncio.run(run())


def test_sql_conversion_job_repository_saves_error_message() -> None:
    async def run() -> None:
        session = _FakeSession(execute_results=[])
        repo = SQLConversionJobRepository(session)  # type: ignore[arg-type]
        job = ConversionJob(
            job_id="job-1",
            conversion=ConversionType("docx", "pdf"),
            input_file="uploads/a.docx",
            status=JobStatus.FAILED,
            error_message="conversion failed",
        )

        await repo.save_conversion_job(job)

        assert session.added[0].error_message == "conversion failed"

    asyncio.run(run())
