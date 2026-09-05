from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import OutboxEvent


client = TestClient(app)


def _tenant(name: str) -> tuple[dict, dict[str, str]]:
    tenant = client.post("/api/tenants", json={"name": name}).json()
    return tenant, {"X-Tenant-ID": tenant["id"]}


def test_slo_evaluation_deduplicates_acknowledges_and_auto_resolves_alerts():
    tenant, headers = _tenant("Phase Seven Alerts")
    policy = client.put("/api/operations/slo-policy", headers=headers, json={
        "window_hours": 24,
        "min_screening_success_rate": .95,
        "max_p95_latency_ms": 120000,
        "max_due_outbox": 0,
        "max_failed_outbox": 0,
        "max_stale_approvals": 0,
        "budget_warning_percent": 80,
        "min_canary_samples": 20,
        "max_success_rate_drop": .02,
        "max_latency_regression_percent": 20,
        "max_cost_regression_percent": 15,
    })
    assert policy.status_code == 200

    with session_scope() as db:
        event = OutboxEvent(
            owner_id=tenant["id"], aggregate_type="test", aggregate_id="phase-seven",
            operation="EMAIL_SEND", payload={}, status="FAILED",
            idempotency_key=f"phase7:failed:{tenant['id']}", last_error="provider unavailable",
        )
        db.add(event)
        db.flush()
        event_id = event.id

    first = client.post("/api/operations/evaluate", headers=headers)
    assert first.status_code == 200
    failures = [item for item in first.json()["alerts"] if item["signal"] == "outbox_failures"]
    assert len(failures) == 1
    assert failures[0]["status"] == "OPEN"

    second = client.post("/api/operations/evaluate", headers=headers).json()
    repeated = next(item for item in second["alerts"] if item["signal"] == "outbox_failures")
    assert repeated["id"] == failures[0]["id"]
    assert repeated["occurrences"] == 2

    acknowledged = client.post(
        f"/api/operations/alerts/{repeated['id']}/action", headers=headers, json={"action": "acknowledge"},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"

    with session_scope() as db:
        db.get(OutboxEvent, event_id).status = "COMPLETED"
    recovered = client.post("/api/operations/evaluate", headers=headers).json()
    resolved = next(item for item in recovered["alerts"] if item["signal"] == "outbox_failures")
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolved_at"] is not None
    assert len(client.get("/api/operations/alerts?status=OPEN", headers=headers).json()) == 0


def test_release_gate_blocks_regression_and_allows_healthy_canary_idempotently():
    tenant, headers = _tenant("Phase Seven Canary")
    baseline = {
        "samples": 100, "success_rate": .99, "p95_latency_ms": 1000,
        "cost_per_screening_micros": 100, "outbox_failures": 0,
    }
    blocked = client.post("/api/operations/release-gates", headers=headers, json={
        "release_version": "v0.9.0-bad",
        "baseline": baseline,
        "candidate": {
            "samples": 5, "success_rate": .90, "p95_latency_ms": 1500,
            "cost_per_screening_micros": 140, "outbox_failures": 1,
        },
    })
    assert blocked.status_code == 201
    assert blocked.json()["status"] == "PROMOTION_BLOCKED"
    assert len(blocked.json()["reasons"]) >= 4

    allowed_payload = {
        "release_version": "v0.9.0-good",
        "baseline": baseline,
        "candidate": {
            "samples": 25, "success_rate": .98, "p95_latency_ms": 1100,
            "cost_per_screening_micros": 110, "outbox_failures": 0,
        },
    }
    allowed = client.post("/api/operations/release-gates", headers=headers, json=allowed_payload)
    assert allowed.status_code == 201
    assert allowed.json()["status"] == "PROMOTION_ALLOWED"
    replay = client.post("/api/operations/release-gates", headers=headers, json=allowed_payload)
    assert replay.json()["id"] == allowed.json()["id"]

    gates = client.get("/api/operations/release-gates", headers=headers).json()
    assert {value["release_version"] for value in gates} == {"v0.9.0-bad", "v0.9.0-good"}
    with session_scope() as db:
        assert db.scalar(select(OutboxEvent).where(OutboxEvent.owner_id == tenant["id"])) is None
