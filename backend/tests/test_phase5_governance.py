from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import current_user_id
from app.database import session_scope
from app.main import app
from app.models import AgentRun, OutboxEvent, SourceIngestion
from app.scheduling import add_outbox, process_outbox_event


client = TestClient(app)


def _tenant(name: str) -> dict:
    response = client.post("/api/tenants", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _job(headers: dict[str, str], title: str) -> dict:
    response = client.post("/api/jobs", headers=headers, json={
        "title": title,
        "description": "Yêu cầu Python, FastAPI và PostgreSQL. Ít nhất 2 năm kinh nghiệm.",
        "department": "Engineering",
        "location": "Remote",
    })
    assert response.status_code == 201
    return response.json()


def test_connector_ingestion_is_scoped_consented_idempotent_and_deletable():
    tenant = _tenant("Phase Five Connector")
    headers = {"X-Tenant-ID": tenant["id"]}
    job = _job(headers, "Connector Ingestion")
    policy = client.put("/api/tenant-policy", headers=headers, json={
        "retention_days": 30,
        "monthly_screening_limit": 1,
        "screenings_per_minute": 10,
        "monthly_token_limit": 100000,
        "monthly_cost_limit_micros": 1000000,
        "email_enabled": False,
        "calendar_enabled": False,
    })
    assert policy.status_code == 200
    with session_scope() as db:
        blocked_event = add_outbox(
            db, owner_id=tenant["id"], aggregate_type="application", aggregate_id="policy-check",
            operation="EMAIL_SEND", payload={"to": "nobody@example.com", "subject": "Blocked", "body": "Blocked"},
            idempotency_key=f"phase5-kill-switch:{tenant['id']}",
        )
        blocked_event_id = blocked_event.id
    process_outbox_event(blocked_event_id)
    with session_scope() as db:
        assert db.get(OutboxEvent, blocked_event_id).status == "BLOCKED"

    now = datetime.now(timezone.utc).isoformat()
    created = client.post("/api/source-connectors", headers=headers, json={
        "kind": "ATS",
        "name": "ATS webhook",
        "scopes": ["resumes.write"],
        "consent_basis": "Recruiter approved ATS resume transfer",
        "consented_at": now,
    })
    assert created.status_code == 201
    connector = created.json()
    ingest_payload = {
        "source_ref": "ats-candidate-42",
        "source_uri": "ats://candidate/42",
        "job_id": job["id"],
        "candidate_name": "Connector Candidate",
        "candidate_email": "connector@example.com",
        "resume_text": "4 năm Python FastAPI PostgreSQL, xây dựng REST API production.",
        "candidate_consented_at": now,
    }
    ingest_headers = {"X-Connector-Token": connector["token"]}
    first = client.post(f"/api/source-connectors/{connector['id']}/ingest", headers=ingest_headers, json=ingest_payload)
    assert first.status_code == 201
    assert first.json()["duplicate"] is False
    application_id = first.json()["application"]["id"]

    replay = client.post(f"/api/source-connectors/{connector['id']}/ingest", headers=ingest_headers, json=ingest_payload)
    assert replay.status_code == 201
    assert replay.json()["duplicate"] is True
    assert replay.json()["application"]["id"] == application_id

    over_budget = client.post("/api/applications", headers=headers, json={
        "job_id": job["id"], "candidate_name": "Over Budget",
        "candidate_email": "over@example.com",
        "resume_text": "3 năm Python FastAPI PostgreSQL và REST API.",
    })
    assert over_budget.status_code == 429

    exported = client.get(f"/api/applications/{application_id}/data-export", headers=headers)
    assert exported.status_code == 200
    assert exported.json()["provenance"][0]["source_ref"] == "ats-candidate-42"
    assert exported.json()["application"]["resume_text"] == ingest_payload["resume_text"]

    deleted = client.delete(f"/api/applications/{application_id}/data", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/applications/{application_id}/data-export", headers=headers).status_code == 404
    with session_scope() as db:
        provenance = db.scalar(select(SourceIngestion).where(SourceIngestion.connector_id == connector["id"]))
        assert provenance.application_id is None
        assert provenance.status == "DATA_DELETED"
        assert provenance.source_ref.startswith("deleted:")
        assert provenance.source_uri == ""
        assert provenance.provenance["redacted"] is True


def test_tenant_viewer_is_read_only_and_model_policy_requires_eval_gate():
    tenant = _tenant("Phase Five RBAC")
    headers = {"X-Tenant-ID": tenant["id"]}
    _job(headers, "RBAC Job")
    member = client.post(f"/api/tenants/{tenant['id']}/members", json={
        "user_id": "phase-five-viewer", "role": "VIEWER",
    })
    assert member.status_code == 201

    app.dependency_overrides[current_user_id] = lambda: "phase-five-viewer"
    try:
        assert client.get("/api/jobs", headers=headers).status_code == 200
        denied = client.post("/api/jobs", headers=headers, json={
            "title": "Denied", "description": "This write must be blocked for a viewer account.",
        })
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user_id, None)

    configured = client.put("/api/model-policy", headers=headers, json={
        "champion": {"prompt_profile": "baseline"},
        "challenger": {"prompt_profile": "strict_evidence"},
        "challenger_percent": 25,
    })
    assert configured.status_code == 200
    assert configured.json()["status"] == "DRAFT"
    assert client.post("/api/model-policy/activate", headers=headers).status_code == 409
    evaluated = client.post("/api/model-policy/evaluate", headers=headers)
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "EVALUATED"
    activated = client.post("/api/model-policy/activate", headers=headers)
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"
    application = client.post("/api/applications", headers=headers, json={
        "job_id": client.get("/api/jobs", headers=headers).json()[0]["id"],
        "candidate_name": "Experiment Candidate",
        "candidate_email": "experiment@example.com",
        "resume_text": "4 năm Python FastAPI PostgreSQL và triển khai REST API production.",
    })
    assert application.status_code == 201
    with session_scope() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.application_id == application.json()["id"]))
        assert run.trace_json["experiment_variant"] in {"champion", "challenger"}
