from src.domain.entities.conversion_job import ConversionJob

class FakeDatabaseRepository:
    def __init__(self):
        self.job_table = {}

    async def save_conversion_job(self, job_data: ConversionJob):
        self.job_table[job_data.job_id] = job_data

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.job_table.get(job_id, None)