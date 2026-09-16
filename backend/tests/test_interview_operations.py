import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import ApprovalRequest, OutboxEvent, SchedulingInvitation


client = TestClient(app)


def booked_interview() -> tuple[dict, dict]:
    suffix = uuid4().hex[:8]
    application = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": f"Operations {suffix}",
        "candidate_email": f"ops-{suffix}@example.com",
        "resume_text": "5 năm Python FastAPI PostgreSQL REST API Docker Redis và phỏng vấn hệ thống.",
    }).json()
    # A reminder whose due time already passed is skipped, so the default 1440/60 minute
    # offsets only both survive when the interview sits more than a day out. Taking the first
    # free slot made the suite pass or fail depending on the hour it ran.
    slots = client.get("/api/interviewers/recruiter-1/available-slots").json()
    earliest = datetime.now(timezone.utc) + timedelta(minutes=1440 + 30)
    slot = next((item["start_at"] for item in slots
                 if datetime.fromisoformat(item["start_at"].replace("Z", "+00:00")) > earliest), None)
    assert slot, "expected a free slot more than a day ahead"
    response = client.post(f"/api/applications/{application['id']}/interview", json={
        "slot": slot, "timezone_name": "Asia/Ho_Chi_Minh", "idempotency_key": f"ops-{suffix}",
    })
    assert response.status_code == 201
    return application, response.json()


def test_booking_creates_scheduled_idempotent_reminders_and_reschedule_capability():
    application, interview = booked_interview()
    operations = client.get(f"/api/interviews/{interview['id']}/operations")
    assert operations.status_code == 200
    assert len(operations.json()["reminders"]) == 2
    assert {item["status"] for item in operations.json()["reminders"]} == {"PENDING"}

    with session_scope() as db:
        reschedule = db.scalar(select(SchedulingInvitation).where(
            SchedulingInvitation.application_id == application["id"],
            SchedulingInvitation.purpose == "RESCHEDULE",
        ))
        assert reschedule is not None
        reminders = list(db.scalars(select(OutboxEvent).where(
            OutboxEvent.idempotency_key.like(f"interview-reminder:{interview['id']}:%")
        )))
        assert len({item.idempotency_key for item in reminders}) == 2
        confirmation = db.scalar(select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"interview-confirmation:{interview['id']}"
        ))
        token_match = re.search(r"schedule=([A-Za-z0-9_-]+)", confirmation.payload["body"])
        assert token_match
        reschedule_token = token_match.group(1)

    public = client.get(f"/api/public/scheduling/{reschedule_token}")
    assert public.status_code == 200
    assert public.json()["mode"] == "reschedule"
    next_slot = public.json()["slots"][0]["start_at"]
    moved = client.post(f"/api/public/scheduling/{reschedule_token}", json={
        "slot": next_slot, "timezone_name": "Asia/Ho_Chi_Minh",
    })
    assert moved.status_code == 201
    assert moved.json()["reschedule_count"] == 1


def test_reschedule_limit_creates_human_escalation():
    _, interview = booked_interview()
    policy = client.put("/api/interview-policy", json={
        "reminder_minutes": [60], "max_reschedules": 1, "feedback_due_hours": 24,
    })
    assert policy.status_code == 200
    first_slot = client.get("/api/interviewers/recruiter-1/available-slots").json()[0]["start_at"]
    assert client.put(f"/api/interviews/{interview['id']}", json={
        "slot": first_slot, "timezone_name": "UTC",
    }).status_code == 200
    second_slot = client.get("/api/interviewers/recruiter-1/available-slots").json()[0]["start_at"]
    blocked = client.put(f"/api/interviews/{interview['id']}", json={
        "slot": second_slot, "timezone_name": "UTC",
    })
    assert blocked.status_code == 409
    approvals = client.get("/api/approvals?request_type=ESCALATION").json()
    assert any(item["resource_id"] == interview["id"] and item["payload"]["reason"] == "RESCHEDULE_LIMIT"
               for item in approvals)


def test_structured_scorecards_generate_sourced_conflict_summary():
    _, interview = booked_interview()
    first = client.post(f"/api/interviews/{interview['id']}/scorecards", json={
        "interviewer_email": "one@example.com", "recommendation": "YES", "note": "Strong API design",
        "answers": [{"criterion": "System design", "rating": 5, "evidence": "Explained tradeoffs"}],
    })
    assert first.status_code == 201
    second = client.post(f"/api/interviews/{interview['id']}/scorecards", json={
        "interviewer_email": "two@example.com", "recommendation": "NO", "note": "Weak scaling answer",
        "answers": [{"criterion": "System design", "rating": 2, "evidence": "Missed failure modes"}],
    })
    assert second.status_code == 201
    summary = second.json()["feedback_summary"]
    assert len(summary["sources"]) == 2
    assert any(item["criterion"] == "System design" for item in summary["conflicts"])
    with session_scope() as db:
        approval = db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.resource_id == interview["id"],
            ApprovalRequest.request_type == "ESCALATION",
            ApprovalRequest.dedupe_key.like("%CONFLICTING_FEEDBACK"),
        ))
        assert approval is not None


def test_no_show_is_routed_to_approval_inbox():
    _, interview = booked_interview()
    response = client.post(f"/api/interviews/{interview['id']}/no-show")
    assert response.status_code == 200
    assert response.json()["interview"]["status"] == "NO_SHOW"
    assert response.json()["approval"]["type"] == "ESCALATION"
