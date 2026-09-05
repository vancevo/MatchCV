from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import session_scope
from app.main import app
from app.models import AgentRun, AgentStep, AgentTask, ApprovalRequest, OutboxEvent
from app.operations import readiness_report, tenant_operations_report


client = TestClient(app)


def test_liveness_and_local_readiness_are_separate_probes():
    assert client.get("/api/live").json() == {"status": "ok", "service": "talentflow-api"}
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert {item["name"] for item in response.json()["checks"]} >= {"database", "queue"}


def test_production_readiness_reports_actionable_blockers():
    settings = replace(
        get_settings(),
        environment="production",
        auth_required=False,
        auto_seed=True,
        queue_eager=True,
        database_url="sqlite:///unsafe.db",
        public_app_url="http://localhost:3000",
        integration_provider="local",
    )
    with session_scope() as db:
        report = readiness_report(db, settings, {"configured": True, "reachable": True, "mode": "eager"})
    assert report["status"] == "not_ready"
    failures = {item["name"] for item in report["checks"] if item["status"] == "fail"}
    assert failures >= {
        "authentication", "seed_data", "durable_queue", "database_engine", "public_url", "external_integrations",
    }


def test_readiness_still_returns_a_report_when_database_probe_fails():
    class UnavailableDatabase:
        def execute(self, *_args, **_kwargs):
            raise ConnectionError("database unavailable")

    report = readiness_report(
        UnavailableDatabase(), get_settings(), {"configured": True, "reachable": True, "mode": "eager"},
    )
    assert report["status"] == "not_ready"
    assert next(item for item in report["checks"] if item["name"] == "database")["status"] == "fail"


def test_tenant_operations_metrics_surface_failures_backlog_and_stale_approvals():
    tenant = client.post("/api/tenants", json={"name": "Phase Six Operations"}).json()
    headers = {"X-Tenant-ID": tenant["id"]}
    job = client.post("/api/jobs", headers=headers, json={
        "title": "Operations Engineer",
        "description": "Yêu cầu Python, FastAPI và PostgreSQL. Ít nhất 2 năm kinh nghiệm.",
    }).json()
    created = client.post("/api/applications", headers=headers, json={
        "job_id": job["id"],
        "candidate_name": "Observed Candidate",
        "candidate_email": "observed@example.com",
        "resume_text": "4 năm Python FastAPI PostgreSQL và xây dựng REST API production.",
    })
    assert created.status_code == 201

    with session_scope() as db:
        run = AgentRun(
            owner_id=tenant["id"], application_id=created.json()["id"], status="COMPLETED",
            idempotency_key=f"phase6:run:{tenant['id']}", finished_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        db.add(AgentTask(
            owner_id=tenant["id"], application_id=created.json()["id"], run_id=run.id,
            status="COMPLETED", idempotency_key=f"phase6:task:{tenant['id']}",
            finished_at=datetime.now(timezone.utc),
        ))
        db.add(AgentStep(owner_id=tenant["id"], run_id=run.id, node="screening", status="COMPLETED", latency_ms=125))
        db.add(OutboxEvent(
            owner_id=tenant["id"], aggregate_type="application", aggregate_id=created.json()["id"],
            operation="EMAIL_SEND", payload={}, status="FAILED", idempotency_key=f"phase6:failed:{tenant['id']}",
            last_error="provider unavailable",
        ))
        db.add(ApprovalRequest(
            owner_id=tenant["id"], request_type="POLICY_VIOLATION", resource_id=created.json()["id"],
            title="Stale exception", summary="Requires a human decision", status="PENDING",
            payload={}, resolution={}, dedupe_key=f"phase6:approval:{tenant['id']}",
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        ))

    response = client.get("/api/operations/metrics?hours=48", headers=headers)
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["status"] == "degraded"
    assert metrics["screening"]["tasks"]["COMPLETED"] >= 1
    assert metrics["screening"]["terminal_success_rate"] == 1.0
    assert metrics["outbox"]["events"]["FAILED"] == 1
    assert metrics["approvals"]["older_than_24h"] == 1

    with session_scope() as db:
        direct = tenant_operations_report(db, tenant["id"], hours=48)
    assert direct["usage"]["screenings"] >= 1
