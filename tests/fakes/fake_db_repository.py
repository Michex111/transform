from src.domain.conversions.entities.conversion_job import ConversionJob

class FakeDatabaseRepository:
    def __init__(self):
        self.job_table = {}

    async def save_conversion_job(self, job_data: ConversionJob):
        self.job_table[job_data.job_id] = job_data

    async def update_conversion_job(self, job: ConversionJob):
        self.job_table[job.job_id] = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.job_table.get(job_id, None)

    async def list_user_history(
        self,
        user_id: int,
        offset: int,
        limit: int,
        since=None,
    ) -> tuple[list[ConversionJob], int]:
        rows = [j for j in self.job_table.values() if j.user_id == user_id]
        if since is not None:
            # Best-effort: filter by a stable created-at when present.
            rows = [
                j for j in rows
                if getattr(getattr(j, "created_at", None), "date", None) is not None
            ]
        return rows[offset:offset + limit], len(rows)

    async def delete_job(self, job_id: str, user_id: int) -> bool:
        job = self.job_table.get(job_id)
        if job is None or job.user_id != user_id:
            return False
        del self.job_table[job_id]
        return True

    async def list_user_active_jobs(
        self,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionJob], int]:
        from src.domain.conversions.value_object.job_status import JobStatus
        rows = [
            j for j in self.job_table.values()
            if j.user_id == user_id and j.status in (
                JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.AWAITING_UPLOAD,
            )
        ]
        return rows[offset:offset + limit], len(rows)