from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import OperationalAlert, OutboxEvent
from scripts.promote_release import main as promote_release


client = TestClient(app)


def test_release_cli_fails_closed_when_configuration_is_missing(monkeypatch):
    for name in ("TALENTFLOW_API_URL", "TALENTFLOW_ACCESS_TOKEN", "TALENTFLOW_TENANT_ID", "TALENTFLOW_RELEASE_VERSION"):
        monkeypatch.delenv(name, raising=False)
    assert promote_release() == 1


def _tenant(name: str) -> tuple[dict, dict[str, str]]:
    tenant = client.post("/api/tenants", json={"name": name}).json()
    return tenant, {"X-Tenant-ID": tenant["id"]}


def _failed_event(owner_id: str, suffix: str) -> str:
    with session_scope() as db:
        event = OutboxEvent(
            owner_id=owner_id, aggregate_type="test", aggregate_id=suffix,
            operation="EMAIL_SEND", payload={}, status="FAILED",
            idempotency_key=f"phase8:failed:{owner_id}:{suffix}", last_error="provider unavailable",
        )
        db.add(event)
        db.flush()
        return event.id


def test_scheduled_sweep_delivers_one_notification_per_alert_episode():
    tenant, headers = _tenant("Phase Eight Notifications")
    configured = client.put("/api/operations/slo-policy", headers=headers, json={
        "window_hours": 24, "min_screening_success_rate": .95, "max_p95_latency_ms": 120000,
        "max_due_outbox": 0, "max_failed_outbox": 0, "max_stale_approvals": 0,
        "budget_warning_percent": 80, "min_canary_samples": 20, "max_success_rate_drop": .02,
        "max_latency_regression_percent": 20, "max_cost_regression_percent": 15,
        "notifications_enabled": True, "notification_email": "oncall@example.com",
    })
    assert configured.status_code == 200
    failed_id = _failed_event(tenant["id"], "notification")

    first = client.post("/api/operations/sweep", headers=headers)
    assert first.status_code == 200
    assert first.json()["notifications_dispatched"] == 1
    second = client.post("/api/operations/sweep", headers=headers)
    assert second.json()["notifications_dispatched"] == 0
    with session_scope() as db:
        notifications = list(db.scalars(select(OutboxEvent).where(
            OutboxEvent.owner_id == tenant["id"], OutboxEvent.aggregate_type == "operational_alert",
        )))
        assert len(notifications) == 1
        assert notifications[0].status == "COMPLETED"
        assert notifications[0].payload["to"] == "oncall@example.com"
        db.get(OutboxEvent, failed_id).status = "COMPLETED"

    client.post("/api/operations/sweep", headers=headers)
    with session_scope() as db:
        assert db.scalar(select(OperationalAlert).where(
            OperationalAlert.owner_id == tenant["id"], OperationalAlert.signal == "outbox_failures",
        )).status == "RESOLVED"
        db.get(OutboxEvent, failed_id).status = "FAILED"
    reopened = client.post("/api/operations/sweep", headers=headers)
    assert reopened.json()["notifications_dispatched"] == 1
    with session_scope() as db:
        assert len(list(db.scalars(select(OutboxEvent).where(
            OutboxEvent.owner_id == tenant["id"], OutboxEvent.aggregate_type == "operational_alert",
        )))) == 2


def test_promotion_endpoint_enforces_gate_readiness_and_active_critical_alerts():
    tenant, headers = _tenant("Phase Eight Promotion")
    baseline = {"samples": 100, "success_rate": .99, "p95_latency_ms": 1000,
                "cost_per_screening_micros": 100, "outbox_failures": 0}
    candidate = {"samples": 25, "success_rate": .98, "p95_latency_ms": 1100,
                 "cost_per_screening_micros": 110, "outbox_failures": 0}
    gate = client.post("/api/operations/release-gates", headers=headers, json={
        "release_version": "v1.0.0-canary", "baseline": baseline, "candidate": candidate,
    }).json()
    assert gate["status"] == "PROMOTION_ALLOWED"

    failed_id = _failed_event(tenant["id"], "promotion")
    client.post("/api/operations/evaluate", headers=headers)
    blocked = client.post(f"/api/operations/release-gates/{gate['id']}/promote", headers=headers)
    assert blocked.status_code == 409
    assert "critical operational alerts" in blocked.json()["detail"]

    with session_scope() as db:
        db.get(OutboxEvent, failed_id).status = "COMPLETED"
    client.post("/api/operations/evaluate", headers=headers)
    promoted = client.post(f"/api/operations/release-gates/{gate['id']}/promote", headers=headers)
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "PROMOTED"
    assert promoted.json()["promoted_at"] is not None
    assert promoted.json()["promoted_by"]
    replay = client.post(f"/api/operations/release-gates/{gate['id']}/promote", headers=headers)
    assert replay.json()["promoted_at"] == promoted.json()["promoted_at"]

    bad_gate = client.post("/api/operations/release-gates", headers=headers, json={
        "release_version": "v1.0.1-bad", "baseline": baseline,
        "candidate": {**candidate, "samples": 1, "outbox_failures": 1},
    }).json()
    assert client.post(f"/api/operations/release-gates/{bad_gate['id']}/promote", headers=headers).status_code == 409
