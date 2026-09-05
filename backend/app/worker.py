from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select

from .agentic import calibrate_screening, maybe_create_shortlist, record_screening_artifact
from .database import session_scope
from .llm import generate_interview_kit_ai, screen_candidate_ai
from .models import AgentRun, AgentStep, AgentTask, Application, AuditLog, BatchItem, CriteriaVersion, Job, UploadBatch
from .statuses import ApplicationStatus, BatchItemStatus, BatchStatus, RunStatus, TaskStatus
from .workflow import screening_pipeline
from .governance import enforce_screening_budget, record_screening_usage, select_model_variant


def sync_batch_status(db, batch_id: str) -> None:
    batch = db.get(UploadBatch, batch_id)
    if not batch:
        return
    statuses = list(db.scalars(select(BatchItem.status).where(BatchItem.batch_id == batch_id)))
    batch.completed = statuses.count(BatchItemStatus.COMPLETED.value)
    batch.failed = statuses.count(BatchItemStatus.FAILED.value)
    batch.skipped = statuses.count(BatchItemStatus.DUPLICATE.value)
    terminal = batch.completed + batch.failed + batch.skipped
    if terminal < batch.total:
        batch.status = BatchStatus.PROCESSING.value
    elif batch.failed and not (batch.completed or batch.skipped):
        batch.status = BatchStatus.FAILED.value
    elif batch.failed:
        batch.status = BatchStatus.PARTIAL.value
    else:
        batch.status = BatchStatus.COMPLETED.value


def _step(db, *, owner_id: str, run_id: str, node: str, status: str, attempt: int,
          started: float, error: str | None = None, metadata: dict | None = None) -> None:
    db.add(AgentStep(
        owner_id=owner_id,
        run_id=run_id,
        node=node,
        status=status,
        attempt=attempt,
        latency_ms=round((perf_counter() - started) * 1000),
        error=error,
        metadata_json=metadata or {},
    ))


async def _execute(task_id: str) -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        if not task or task.status == TaskStatus.COMPLETED.value:
            return
        application = db.get(Application, task.application_id)
        run = db.get(AgentRun, task.run_id)
        item = db.get(BatchItem, task.batch_item_id) if task.batch_item_id else None
        job = db.get(Job, application.job_id) if application else None
        if not all((application, run, job)):
            raise RuntimeError("Screening task references missing data")

        enforce_screening_budget(db, task.owner_id)
        variant, variant_config = select_model_variant(
            db, task.owner_id, application.resume_checksum or application.id
        )
        if variant_config.get("model"):
            run.model = str(variant_config["model"])
        run.prompt_version = f"screening-v2:{variant_config.get('prompt_profile', 'baseline')}+calibration-v1"

        task.attempts += 1
        task.status = TaskStatus.RUNNING.value
        task.started_at = now
        task.last_error = None
        run.status = RunStatus.RUNNING.value
        run.current_node = "screen_candidate"
        run.started_at = run.started_at or now
        run.error = None
        if item:
            item.status = BatchItemStatus.PROCESSING.value
            item.error = None
        application.status = ApplicationStatus.PROCESSING.value
        application.pipeline = screening_pipeline("Candidate Extracted")
        criteria_version = db.get(CriteriaVersion, run.criteria_version_id) if run.criteria_version_id else None
        payload = {
            "attempt": task.attempts,
            "owner_id": task.owner_id,
            "application_id": application.id,
            "run_id": run.id,
            "batch_id": task.batch_id,
            "batch_item_id": item.id if item else None,
            "resume_text": application.resume_text,
            "requirements": criteria_version.criteria if criteria_version else job.requirements,
            "job_title": job.title,
            "candidate_name": application.candidate_name,
            "candidate_email": application.candidate_email,
            "experiment_variant": variant,
            "variant_config": variant_config,
        }

    screening_started = perf_counter()
    try:
        result = await screen_candidate_ai(
            payload["resume_text"], payload["requirements"], payload["candidate_name"], payload["candidate_email"],
            model_override=str(payload["variant_config"].get("model", "")).strip() or None,
            prompt_profile=str(payload["variant_config"].get("prompt_profile", "baseline")),
        )
        with session_scope() as db:
            _step(db, owner_id=payload["owner_id"], run_id=payload["run_id"], node="screen_candidate",
                  status="COMPLETED", attempt=payload["attempt"], started=screening_started,
                  metadata={"source": result.get("screening_source", "rules")})
            run = db.get(AgentRun, payload["run_id"])
            if run:
                run.current_node = "generate_interview_kit"

        kit_started = perf_counter()
        result["interview_kit"] = await generate_interview_kit_ai(
            payload["resume_text"], payload["requirements"], result, payload["job_title"],
            payload["candidate_name"], payload["candidate_email"],
        )
        result, embedding = calibrate_screening(result, payload["resume_text"], payload["requirements"])
        finished = datetime.now(timezone.utc)
        with session_scope() as db:
            task = db.get(AgentTask, task_id)
            application = db.get(Application, payload["application_id"])
            run = db.get(AgentRun, payload["run_id"])
            item = db.get(BatchItem, payload["batch_item_id"]) if payload["batch_item_id"] else None
            if not all((task, application, run)):
                raise RuntimeError("Screening task state disappeared")
            _step(db, owner_id=payload["owner_id"], run_id=run.id, node="generate_interview_kit",
                  status="COMPLETED", attempt=task.attempts, started=kit_started,
                  metadata={"source": result["interview_kit"].get("source", "rules")})
            application.screening = result
            application.status = ApplicationStatus.WAITING_REVIEW.value
            application.pipeline = screening_pipeline("Interview Kit Generated")
            if item:
                item.status = BatchItemStatus.COMPLETED.value
                item.error = None
            task.status = TaskStatus.COMPLETED.value
            task.finished_at = finished
            run.status = RunStatus.COMPLETED.value
            run.current_node = "completed"
            run.finished_at = finished
            run.error = None
            screening_source = result.get("screening_source", "rules")
            if screening_source == "rules":
                run.provider = "rules"
                run.fallback_reason = run.fallback_reason or "openrouter_unavailable_or_invalid_response"
            else:
                run.provider = "openrouter"
                run.fallback_reason = None
            run.input_tokens = max(1, len(payload["resume_text"]) // 4) if run.provider == "openrouter" else 0
            run.output_tokens = max(1, len(str(result)) // 4) if run.provider == "openrouter" else 0
            run.cost_micros = 0
            run.trace_json = {
                "model": run.model,
                "prompt_version": run.prompt_version,
                "tools": ["verified_evidence", "hash_embedding", "score_calibration"],
                "calibration_version": result["calibration_version"],
                "fallback_reason": run.fallback_reason,
                "experiment_variant": payload["experiment_variant"],
            }
            record_screening_usage(db, payload["owner_id"], run.input_tokens, run.output_tokens, run.cost_micros)
            record_screening_artifact(db, application, run, result, embedding)
            db.add(AuditLog(owner_id=payload["owner_id"], application_id=application.id,
                            action="SCREENING_COMPLETED",
                            metadata_json={"score": result["final_score"], "run_id": run.id, "attempt": task.attempts}))
            if task.batch_id:
                sync_batch_status(db, task.batch_id)
            maybe_create_shortlist(db, application.job_id, application.owner_id)
    except Exception as exc:
        message = str(exc)[:2000] or exc.__class__.__name__
        with session_scope() as db:
            task = db.get(AgentTask, task_id)
            if not task:
                raise
            application = db.get(Application, task.application_id)
            run = db.get(AgentRun, task.run_id)
            item = db.get(BatchItem, task.batch_item_id) if task.batch_item_id else None
            final_failure = task.attempts >= task.max_attempts
            task.status = TaskStatus.FAILED.value if final_failure else TaskStatus.RETRYING.value
            task.last_error = message
            task.finished_at = datetime.now(timezone.utc) if final_failure else None
            if run:
                run.status = RunStatus.FAILED.value if final_failure else RunStatus.QUEUED.value
                run.error = message
                run.current_node = "failed" if final_failure else "retry_scheduled"
                run.finished_at = task.finished_at
                _step(db, owner_id=task.owner_id, run_id=run.id, node=run.current_node,
                      status="FAILED", attempt=task.attempts, started=screening_started, error=message)
            if item:
                item.status = BatchItemStatus.FAILED.value if final_failure else BatchItemStatus.QUEUED.value
                item.error = message
            if application:
                application.status = (ApplicationStatus.SCREENING_FAILED.value if final_failure
                                      else ApplicationStatus.PROCESSING.value)
                application.pipeline = screening_pipeline("Candidate Extracted", "Evidence Generated" if final_failure else None)
            if task.batch_id:
                sync_batch_status(db, task.batch_id)
        raise


def process_screening_task(task_id: str) -> None:
    asyncio.run(_execute(task_id))


def mark_enqueue_failed(task_id: str, error: str) -> None:
    message = f"Queue unavailable: {error[:500]}"
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        if not task:
            return
        application = db.get(Application, task.application_id)
        run = db.get(AgentRun, task.run_id)
        item = db.get(BatchItem, task.batch_item_id) if task.batch_item_id else None
        task.status = TaskStatus.FAILED.value
        task.last_error = message
        task.finished_at = datetime.now(timezone.utc)
        if run:
            run.status = RunStatus.FAILED.value
            run.current_node = "enqueue_failed"
            run.error = message
            run.finished_at = task.finished_at
        if item:
            item.status = BatchItemStatus.FAILED.value
            item.error = message
        if application:
            application.status = ApplicationStatus.SCREENING_FAILED.value
            application.pipeline = screening_pipeline("Candidate Extracted", "Evidence Generated")
        db.add(AuditLog(owner_id=task.owner_id, application_id=task.application_id,
                        action="QUEUE_ENQUEUE_FAILED", metadata_json={"task_id": task.id, "error": message}))
        if task.batch_id:
            sync_batch_status(db, task.batch_id)


def handle_job_failure(job, _connection, _exc_type, exc_value, _traceback, *_args, **_kwargs) -> None:
    """Reconcile database state when RQ terminates a job, including timeouts."""
    task_id = str(job.args[0]) if job.args else ""
    message = str(exc_value)[:2000] or "RQ worker failure"
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        if not task or task.status == TaskStatus.COMPLETED.value:
            return
        application = db.get(Application, task.application_id)
        run = db.get(AgentRun, task.run_id)
        item = db.get(BatchItem, task.batch_item_id) if task.batch_item_id else None
        task.status = TaskStatus.FAILED.value
        task.attempts = max(task.attempts, task.max_attempts)
        task.last_error = message
        task.finished_at = datetime.now(timezone.utc)
        if run:
            run.status = RunStatus.FAILED.value
            run.current_node = "worker_failure"
            run.error = message
            run.finished_at = task.finished_at
            db.add(AgentStep(owner_id=task.owner_id, run_id=run.id, node="worker_failure", status="FAILED",
                             attempt=task.attempts, error=message, metadata_json={"rq_job_id": job.id}))
        if item:
            item.status = BatchItemStatus.FAILED.value
            item.error = message
        if application:
            application.status = ApplicationStatus.SCREENING_FAILED.value
            application.pipeline = screening_pipeline("Candidate Extracted", "Evidence Generated")
        if task.batch_id:
            sync_batch_status(db, task.batch_id)
