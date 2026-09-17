from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import current_user_id
from app.config import Settings, get_settings
from app.database import session_scope
from app.llm import extract_requirements_ai
from app.main import app
from app.models import Application, Interview, Job


client = TestClient(app)


def test_config_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "sometimes")
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        Settings.from_env()


def test_config_requires_redis_when_queue_is_not_eager(monkeypatch):
    monkeypatch.setenv("QUEUE_EAGER", "false")
    monkeypatch.setenv("REDIS_URL", "")
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        Settings.from_env()


def test_booking_rejects_datetime_without_timezone():
    response = client.post("/api/applications/app-001/interview", json={"slot": "2030-01-01T09:00:00"})
    assert response.status_code == 422


def test_auth_requires_token_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            current_user_id(None)
        assert exc.value.status_code == 401
    finally:
        monkeypatch.setenv("AUTH_REQUIRED", "false")
        get_settings.cache_clear()


def test_tenant_isolation_with_dependency_override():
    other_owner = "tenant-foundation-test"
    with session_scope() as db:
        db.add(Job(
            id="tenant-private-job",
            owner_id=other_owner,
            title="Private Job",
            department="Security",
            location="Remote",
            description="Private job description for tenant isolation",
            requirements={},
        ))

    app.dependency_overrides[current_user_id] = lambda: other_owner
    try:
        visible = client.get("/api/jobs").json()
        assert [job["id"] for job in visible] == ["tenant-private-job"]
        assert client.get("/api/dashboard").json()["metrics"]["candidates"] == 0
    finally:
        app.dependency_overrides.clear()

    assert "tenant-private-job" not in {job["id"] for job in client.get("/api/jobs").json()}


def test_application_filter_and_pagination():
    response = client.get("/api/applications", params={"status": "WAITING_REVIEW", "q": "Nguyễn", "limit": 1})
    assert response.status_code == 200
    assert len(response.json()) <= 1
    assert all(item["status"] == "WAITING_REVIEW" for item in response.json())
    invalid = client.get("/api/applications", params={"limit": 101})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_job_and_audit_filters_support_pagination():
    jobs = client.get("/api/jobs", params={"status": "OPEN", "q": "Backend", "limit": 1})
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1
    logs = client.get("/api/audit-logs", params={"action": "SCREENING_COMPLETED", "limit": 1})
    assert logs.status_code == 200
    assert len(logs.json()) <= 1
    assert all(item["action"] == "SCREENING_COMPLETED" for item in logs.json())


def test_openrouter_failure_uses_rules(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.llm.extract_requirements_schema_ai", unavailable)
    monkeypatch.setattr("app.llm._complete", unavailable)
    import asyncio

    result = asyncio.run(extract_requirements_ai("Yêu cầu Python, FastAPI. Ít nhất 2 năm kinh nghiệm."))
    assert result["extraction_source"] == "rules"
    assert result["required_skills"] == ["Python", "FastAPI"]


def test_upload_limit_comes_from_validated_config(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/application-batches",
            data={"job_id": "job-backend-01"},
            files={"files": ("large.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
        )
        assert response.status_code == 202
        assert response.json()["failed"] == 1
        assert "1 MB" in response.json()["items"][0]["error"]
    finally:
        monkeypatch.setenv("MAX_UPLOAD_MB", "10")
        get_settings.cache_clear()


def test_database_constraint_prevents_booking_race():
    with session_scope() as db:
        application = db.scalar(select(Application).where(Application.owner_id == "00000000-0000-0000-0000-000000000001"))
        assert application is not None
        slot = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
        db.add(Interview(owner_id=application.owner_id, application_id=application.id, start_at=slot,
                         end_at=slot + timedelta(hours=1), meeting_url="https://meet.example/first"))

    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(Interview(owner_id=application.owner_id, application_id=application.id, start_at=slot,
                             end_at=slot + timedelta(hours=1), meeting_url="https://meet.example/second"))
