from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import session_scope
from app.main import app
from app.models import AgentRun, AgentStep, AgentTask, Application, BatchItem
from app.worker import process_screening_task


client = TestClient(app)


def _upload(filename: str, text: str):
    return client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files={"files": (filename, text.encode(), "text/plain")},
    )


def test_batch_persists_task_before_worker_and_can_resume(monkeypatch):
    async def leave_queued(task_id: str) -> str:
        return f"test-queue:{task_id}"

    monkeypatch.setattr("app.main.enqueue_screening_async", leave_queued)
    response = _upload(
        "async-resume.txt",
        "Async Resume Candidate\nasync-resume@example.com\n6 năm Python FastAPI PostgreSQL REST API Docker Redis.",
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PROCESSING"
    assert body["items"][0]["status"] == "QUEUED"

    task_id = body["items"][0]["task_id"]
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        assert task is not None
        assert task.idempotency_key
        run = db.get(AgentRun, task.run_id)
        assert run.status == "QUEUED"
        assert run.prompt_version == "screening-v1+interview-kit-v1"

    process_screening_task(task_id)
    completed = client.get(f"/api/application-batches/{body['batch_id']}").json()
    assert completed["status"] == "COMPLETED"
    assert completed["completed"] == 1
    assert completed["items"][0]["application"]["status"] == "WAITING_REVIEW"

    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == task.run_id)))
        assert task.status == "COMPLETED"
        assert [step.node for step in steps] == ["screen_candidate", "generate_interview_kit"]

    run_response = client.get(f"/api/agent-runs/{task.run_id}")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "COMPLETED"


def test_duplicate_checksum_is_skipped_without_new_application():
    text = "Duplicate Candidate\nduplicate-phase2@example.com\n4 năm Python FastAPI PostgreSQL REST API."
    first = _upload("duplicate-first.txt", text)
    assert first.status_code == 202
    application_id = first.json()["items"][0]["application"]["id"]
    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(Application).where(Application.id == application_id))

    second = _upload("duplicate-second.txt", text)
    assert second.status_code == 202
    assert second.json()["status"] == "COMPLETED"
    assert second.json()["skipped"] == 1
    assert second.json()["items"][0]["status"] == "DUPLICATE"
    assert second.json()["items"][0]["application"]["id"] == application_id
    with session_scope() as db:
        after = db.scalar(select(func.count()).select_from(Application).where(Application.id == application_id))
    assert before == after == 1


def test_failed_task_reaches_dlq_state_and_can_be_requeued(monkeypatch):
    async def leave_queued(task_id: str) -> str:
        return f"test-queue:{task_id}"

    async def provider_failure(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.main.enqueue_screening_async", leave_queued)
    response = _upload(
        "retry-candidate.txt",
        "Retry Candidate\nretry-phase2@example.com\n3 năm Python FastAPI PostgreSQL REST API.",
    )
    body = response.json()
    task_id = body["items"][0]["task_id"]
    monkeypatch.setattr("app.worker.screen_candidate_ai", provider_failure)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            process_screening_task(task_id)

    failed = client.get(f"/api/application-batches/{body['batch_id']}").json()
    assert failed["status"] == "FAILED"
    assert failed["items"][0]["status"] == "FAILED"
    with session_scope() as db:
        task = db.get(AgentTask, task_id)
        assert task.status == "FAILED"
        assert task.attempts == task.max_attempts == 3
        assert db.get(Application, task.application_id).status == "SCREENING_FAILED"

    retried = client.post(
        f"/api/application-batches/{body['batch_id']}/items/{body['items'][0]['id']}/retry"
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "PROCESSING"
    assert retried.json()["items"][0]["status"] == "QUEUED"
    with session_scope() as db:
        item = db.get(BatchItem, body["items"][0]["id"])
        task = db.get(AgentTask, item.task_id)
        assert task.id != task_id
        assert task.attempts == 0
        assert task.status == "QUEUED"
        assert db.get(AgentTask, task_id).status == "FAILED"
        assert db.get(AgentRun, task.run_id).trigger == "manual_retry"


def test_enqueue_failure_is_durable_and_visible(monkeypatch):
    async def queue_unavailable(_task_id: str) -> str:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app.main.enqueue_screening_async", queue_unavailable)
    response = _upload(
        "queue-unavailable.txt",
        "Queue Failure Candidate\nqueue-failure@example.com\n3 năm Python FastAPI PostgreSQL REST API.",
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failed"] == 1
    assert body["items"][0]["status"] == "FAILED"
    assert "Queue unavailable" in body["items"][0]["error"]
    with session_scope() as db:
        task = db.get(AgentTask, body["items"][0]["task_id"])
        assert task.status == "FAILED"
        assert db.get(AgentRun, task.run_id).current_node == "enqueue_failed"
