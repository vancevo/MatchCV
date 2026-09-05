from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.config import Settings
from app.main import app
from app.models import OutboxEvent, SchedulingInvitation
from app.scheduling import TokenCipher


client = TestClient(app)


def _application() -> dict:
    response = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": "Scheduling Candidate",
        "candidate_email": "schedule@example.com",
        "resume_text": "4 năm Python FastAPI REST API PostgreSQL Docker Redis và vận hành production.",
    })
    assert response.status_code == 201
    return response.json()


def test_connection_tokens_are_encrypted_at_rest():
    cipher = TokenCipher("unit-test-secret")
    encrypted = cipher.encrypt("refresh-token-value")
    assert encrypted != "refresh-token-value"
    assert cipher.decrypt(encrypted) == "refresh-token-value"


def test_self_scheduling_link_is_hashed_single_use_and_dispatches_outbox():
    application = _application()
    created = client.post(f"/api/applications/{application['id']}/scheduling-invitations", json={
        "timezone_name": "Asia/Ho_Chi_Minh", "duration_minutes": 60, "expires_in_hours": 24,
    })
    assert created.status_code == 201
    public_url = created.json()["public_url"]
    token = public_url.split("schedule=", 1)[1]

    with session_scope() as db:
        invitation = db.scalar(select(SchedulingInvitation).where(SchedulingInvitation.application_id == application["id"]))
        assert invitation is not None
        assert token not in invitation.token_hash
        invitation_outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == invitation.id))
        assert invitation_outbox.status == "COMPLETED"

    schedule = client.get(f"/api/public/scheduling/{token}")
    assert schedule.status_code == 200
    assert schedule.json()["timezone"] == "Asia/Ho_Chi_Minh"
    slot = schedule.json()["slots"][0]["start_at"]
    second_application = _application()
    second_invitation = client.post(f"/api/applications/{second_application['id']}/scheduling-invitations", json={
        "timezone_name": "Asia/Ho_Chi_Minh", "duration_minutes": 60, "expires_in_hours": 24,
    }).json()
    second_token = second_invitation["public_url"].split("schedule=", 1)[1]
    booked = client.post(f"/api/public/scheduling/{token}", json={
        "slot": slot, "timezone_name": "Asia/Ho_Chi_Minh", "idempotency_key": f"candidate-{application['id']}",
    })
    assert booked.status_code == 201
    assert booked.json()["status"] == "PENDING_CONFIRMATION"
    assert booked.json()["meeting_url"] == ""
    second_slots = client.get(f"/api/public/scheduling/{second_token}").json()["slots"]
    assert slot not in {item["start_at"] for item in second_slots}
    confirmed = client.post(f"/api/interviews/{booked.json()['id']}/confirm", json={"note": "Approved by HR"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "SCHEDULED"
    assert confirmed.json()["meeting_url"].startswith("https://meet.example/")
    assert booked.json()["provider"] == "local"
    assert client.get(f"/api/public/scheduling/{token}").status_code == 404

    outbox = client.get("/api/outbox").json()
    related = [item for item in outbox if item["status"] == "COMPLETED"]
    assert len(related) >= 3  # invitation email, calendar event, confirmation email


def test_direct_booking_replay_is_idempotent():
    application = _application()
    slot = client.get("/api/interviewers/recruiter-1/available-slots").json()[0]["start_at"]
    payload = {"slot": slot, "timezone_name": "UTC", "idempotency_key": f"direct-{application['id']}"}
    first = client.post(f"/api/applications/{application['id']}/interview", json=payload)
    second = client.post(f"/api/applications/{application['id']}/interview", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    next_slot = client.get("/api/interviewers/recruiter-1/available-slots").json()[0]["start_at"]
    moved = client.put(f"/api/interviews/{first.json()['id']}", json={"slot": next_slot, "timezone_name": "UTC"})
    assert moved.status_code == 200
    assert moved.json()["status"] == "SCHEDULED"
    assert moved.json()["start_at"] == next_slot.replace("+00:00", "Z")

    cancelled = client.delete(f"/api/interviews/{first.json()['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert client.delete(f"/api/interviews/{first.json()['id']}").json()["status"] == "CANCELLED"


def test_email_templates_are_versioned():
    first = client.post("/api/email-templates", json={
        "key": "scheduling_invitation", "subject": "Chọn lịch {candidate_name}",
        "body_text": "Mở đường dẫn này để chọn lịch: {public_url}",
    })
    second = client.post("/api/email-templates", json={
        "key": "scheduling_invitation", "subject": "Mời phỏng vấn {candidate_name}",
        "body_text": "Link chọn lịch mới của bạn: {public_url}",
    })
    assert first.status_code == second.status_code == 201
    assert second.json()["version"] == first.json()["version"] + 1
    versions = [item for item in client.get("/api/email-templates").json() if item["key"] == "scheduling_invitation"]
    assert sum(item["active"] for item in versions) == 1


def test_provider_webhook_is_deduplicated():
    payload = {"id": "delivery-event-001", "type": "email.delivered"}
    assert client.post("/api/webhooks/google", json=payload).json() == {"accepted": True, "duplicate": False}
    assert client.post("/api/webhooks/google", json=payload).json() == {"accepted": True, "duplicate": True}


def test_oauth_start_fails_closed_without_client_credentials():
    response = client.post("/api/integrations/google/authorize")
    assert response.status_code == 409
    assert "not configured" in response.json()["detail"]


def test_real_provider_requires_durable_queue_or_free_demo_override_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTEGRATION_PROVIDER", "google")
    monkeypatch.setenv("INTEGRATION_TOKEN_SECRET", "test-token-secret")
    monkeypatch.setenv("OAUTH_STATE_SECRET", "test-state-secret")
    monkeypatch.setenv("QUEUE_EAGER", "true")
    try:
        import pytest
        with pytest.raises(RuntimeError, match="ALLOW_EAGER_REAL_INTEGRATIONS"):
            Settings.from_env()
        monkeypatch.setenv("ALLOW_EAGER_REAL_INTEGRATIONS", "true")
        assert Settings.from_env().allow_eager_real_integrations is True
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("INTEGRATION_PROVIDER", "local")
        monkeypatch.delenv("ALLOW_EAGER_REAL_INTEGRATIONS", raising=False)
