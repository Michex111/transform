import asyncio
from src.infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import JobProcess

class ConverterWorker:
    def __init__(self, context: WorkerContext, process_job: JobProcess):
        self.context = context
        self.process_job = process_job
        self._running = False

    async def run(self):
        self._running = True
        log_context = self.context.get_log_context()
        worker_logger.info("Converter worker started", extra=log_context)
        
        while self._running:
            try:
                job = await self.context.queue_port.fetch_job()
                if job is None:
                    await asyncio.sleep(1)  # Sleep briefly if no job is available
                    continue
                
                message_id, job = job
                job_log_context = self.context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
                worker_logger.info(f"Fetched job {job.job_id} for processing", extra=job_log_context)
                try:
                    await self.process_job(self.context, job)
                except Exception as e:
                    await self.context.queue_port.fail_job(message_id, str(e))
                    worker_logger.error(f"Error processing job {job.job_id}: {str(e)}", extra=job_log_context)
                    continue
                else:
                    await self.context.queue_port.acknowledge_job(message_id)
                    worker_logger.info(f"Job {job.job_id} completed successfully", extra=job_log_context)
            except asyncio.CancelledError:
                worker_logger.info("Converter worker received shutdown signal", extra=log_context)
                self.stop()
                return
            except Exception as loop_error:
                worker_logger.critical(f"Unexpected error in worker loop: {str(loop_error)}", extra=log_context)
                await asyncio.sleep(5)  # Sleep before retrying to avoid tight error loop
    
    def stop(self):
        self._running = False
        log_context = self.context.get_log_context()
        worker_logger.info("Converter worker stopped", extra=log_context)
            
        
            

