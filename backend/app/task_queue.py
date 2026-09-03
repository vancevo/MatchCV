from __future__ import annotations

import asyncio
from uuid import uuid4

from redis import Redis
from rq import Callback, Queue, Retry

from .config import get_settings
from .database import session_scope
from .models import AgentTask


def enqueue_screening(task_id: str) -> str:
    settings = get_settings()
    if settings.queue_eager:
        from .worker import process_screening_task

        for _ in range(settings.task_max_attempts):
            try:
                process_screening_task(task_id)
                break
            except Exception:
                with session_scope() as db:
                    task = db.get(AgentTask, task_id)
                    if not task or task.attempts >= task.max_attempts:
                        break
        job_id = f"eager:{task_id}"
        with session_scope() as db:
            task = db.get(AgentTask, task_id)
            if task:
                task.queue_job_id = job_id
        return job_id

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.queue_name, connection=connection)
    retry_intervals = [min(10 * (3 ** index), 300) for index in range(max(settings.task_max_attempts - 1, 0))]
    options = {
        "job_id": f"screening-{task_id}-{uuid4()}",
        "job_timeout": settings.task_timeout_seconds,
        "result_ttl": 3600,
        "failure_ttl": 7 * 24 * 3600,
        "on_failure": Callback("app.worker.handle_job_failure", timeout=15),
    }
    if settings.task_max_attempts > 1:
        options["retry"] = Retry(max=settings.task_max_attempts - 1, interval=retry_intervals)
    job = queue.enqueue("app.worker.process_screening_task", task_id, **options)
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        if task:
            task.queue_job_id = job.id
    return job.id


async def enqueue_screening_async(task_id: str) -> str:
    return await asyncio.to_thread(enqueue_screening, task_id)


def queue_summary() -> dict:
    settings = get_settings()
    reachable = True
    if not settings.queue_eager:
        try:
            reachable = bool(Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.25, socket_timeout=0.25
            ).ping())
        except Exception:
            reachable = False
    return {
        "mode": "eager" if settings.queue_eager else "redis",
        "queue": settings.queue_name,
        "configured": settings.queue_eager or bool(settings.redis_url),
        "reachable": reachable,
    }
