from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import session_scope
from app.main import app
from app.models import OutboxEvent
from app.scheduling import add_outbox, dispatch_outbox, mail_sandbox_alias, mail_sandbox_recipient_allowed


client = TestClient(app)


def _tenant() -> tuple[dict, dict[str, str]]:
    tenant = client.post("/api/tenants", json={"name": "Mail Sandbox Testers"}).json()
    return tenant, {"X-Tenant-ID": tenant["id"]}


def test_mail_sandbox_generates_numeric_gmail_aliases_and_preserves_recipient():
    tenant, headers = _tenant()
    configured = client.put("/api/mail-sandbox", headers=headers, json={
        "enabled": True,
        "base_email": "vinhvp.khmtk36@gmail.com",
        "max_alias": 20,
    })
    assert configured.status_code == 200
    assert configured.json()["sample_aliases"][:2] == [
        "vinhvp.khmtk36+1@gmail.com", "vinhvp.khmtk36+2@gmail.com",
    ]

    sent = client.post("/api/mail-sandbox/test", headers=headers, json={
        "alias_number": 7,
        "subject": "Sandbox alias 7",
        "body": "This should keep the plus alias in the outbox.",
    })
    assert sent.status_code == 200
    assert sent.json()["recipient"] == "vinhvp.khmtk36+7@gmail.com"
    assert sent.json()["stored_recipient"] == "vinhvp.khmtk36+7@gmail.com"
    assert sent.json()["status"] == "COMPLETED"
    assert sent.json()["simulated"] is True
    with session_scope() as db:
        event = db.get(OutboxEvent, sent.json()["outbox_id"])
        assert event.payload["to"] == "vinhvp.khmtk36+7@gmail.com"

    assert client.post("/api/mail-sandbox/test", headers=headers, json={"alias_number": 21}).status_code == 422


def test_mail_sandbox_blocks_non_whitelisted_outbox_recipient():
    tenant, headers = _tenant()
    client.put("/api/mail-sandbox", headers=headers, json={
        "enabled": True, "base_email": "vinhvp.khmtk36@gmail.com", "max_alias": 5,
    })
    with session_scope() as db:
        allowed = add_outbox(
            db, owner_id=tenant["id"], aggregate_type="test", aggregate_id="allowed",
            operation="EMAIL_SEND", payload={"to": "vinhvp.khmtk36+5@gmail.com", "subject": "ok", "body": "ok"},
            idempotency_key=f"mail-sandbox:allowed:{tenant['id']}",
        )
        blocked = add_outbox(
            db, owner_id=tenant["id"], aggregate_type="test", aggregate_id="blocked",
            operation="EMAIL_SEND", payload={"to": "someone@example.com", "subject": "blocked", "body": "blocked"},
            idempotency_key=f"mail-sandbox:blocked:{tenant['id']}",
        )
        allowed_id, blocked_id = allowed.id, blocked.id
    dispatch_outbox(allowed_id)
    dispatch_outbox(blocked_id)
    with session_scope() as db:
        assert db.get(OutboxEvent, allowed_id).status == "COMPLETED"
        blocked = db.get(OutboxEvent, blocked_id)
        assert blocked.status == "BLOCKED"
        assert "sandbox whitelist" in blocked.last_error


def test_mail_sandbox_matching_accepts_only_base_or_bounded_numeric_aliases():
    class Policy:
        mail_sandbox_enabled = True
        mail_sandbox_base_email = "vinhvp.khmtk36@gmail.com"
        mail_sandbox_max_alias = 10

    policy = Policy()
    assert mail_sandbox_alias(policy.mail_sandbox_base_email, 3) == "vinhvp.khmtk36+3@gmail.com"
    assert mail_sandbox_recipient_allowed(policy, "vinhvp.khmtk36@gmail.com")
    assert mail_sandbox_recipient_allowed(policy, "vinhvp.khmtk36+10@gmail.com")
    assert not mail_sandbox_recipient_allowed(policy, "vinhvp.khmtk36+11@gmail.com")
    assert not mail_sandbox_recipient_allowed(policy, "vinhvp.khmtk36+abc@gmail.com")
    assert not mail_sandbox_recipient_allowed(policy, "other@gmail.com")
