"""SSE (Server-Sent Events) endpoint for real-time conversion progress."""

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventSubscriber
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_event_subscriber,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("/jobs/{job_id}")
async def stream_job_events(
    job_id: str,
    request: Request,
    current_user: CurrentUser,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    subscriber: Annotated[JobEventSubscriber, Depends(get_event_subscriber)],
):
    """
    SSE endpoint that streams conversion job progress events.

    The client connects and receives real-time updates as the job
    progresses through downloading → converting → uploading → complete.
    Previously emitted events are replayed first, then new ones stream in.

    Only the owner of the job may subscribe (ownership enforced).

    Event format:
        event: progress
        data: {"job_id": "...", "status": "PROCESSING", "progress": 50, "message": "converting file"}
    """
    # Ownership check: an authenticated user may only watch their own jobs.
    job = await repository.get_conversion_job(job_id)
    if job is None or job.user_id is None or job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    async def event_generator():
        try:
            # Send initial connection event
            yield f"event: connected\ndata: {json.dumps({'job_id': job_id, 'status': 'connected'})}\n\n"

            terminal_seen = False
            async for _, fields in subscriber.iter_events(job_id):
                if await request.is_disconnected():
                    return
                status_value = fields.get("status", "")
                payload = {
                    "job_id": job_id,
                    "status": status_value,
                    "progress": fields.get("progress", 0),
                    "message": fields.get("message", ""),
                }
                if "compute_duration_ms" in fields:
                    payload["compute_duration_ms"] = fields["compute_duration_ms"]
                if "credits_used" in fields:
                    payload["credits_used"] = fields["credits_used"]

                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"

                if status_value in ("COMPLETED", "FAILED"):
                    terminal_seen = True
                    break

            if not terminal_seen:
                # No terminal event (yet) — keep the connection open with heartbeats
                # until the client disconnects or the job finishes elsewhere.
                while not await request.is_disconnected():
                    yield f": heartbeat\n\n"
                    await asyncio.sleep(15)

        except asyncio.CancelledError:
            logger.info("SSE connection cancelled for job %s", job_id)
        except Exception as e:
            logger.error("SSE error for job %s: %s", job_id, e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
