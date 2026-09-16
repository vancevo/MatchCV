import re

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import AuditLog, EmailTemplate
from app.scheduling import (
    BUILTIN_TEMPLATE_VERSION,
    BUILTIN_TEMPLATES,
    SUPERSEDED_BUILTIN_BODIES,
    local_datetime_label,
    render_template,
)
import app.scheduling as scheduling


client = TestClient(app)
OWNER = "00000000-0000-0000-0000-000000000001"


def _application() -> dict:
    response = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": "Mail Delivery Candidate",
        "candidate_email": "mail-delivery@example.com",
        "resume_text": "5 năm Python FastAPI PostgreSQL Docker Redis REST API và vận hành production.",
    })
    assert response.status_code == 201
    return response.json()


def test_local_datetime_label_reads_as_vietnamese_wall_clock():
    from datetime import datetime, timezone

    # 07:00 UTC is 14:00 in Ho Chi Minh City, and 17/09/2026 is a Thursday.
    label = local_datetime_label(datetime(2026, 9, 17, 7, 0, tzinfo=timezone.utc))
    assert label == "14:00 Thứ Năm, 17/09/2026"


def test_local_datetime_label_falls_back_when_the_zone_is_unknown():
    from datetime import datetime, timezone

    value = datetime(2026, 9, 17, 7, 0, tzinfo=timezone.utc)
    assert local_datetime_label(value, "Mars/Olympus") == local_datetime_label(value)


def test_builtin_copy_supersedes_the_seeded_v1_row_but_spares_custom_copy():
    with session_scope() as db:
        for key, bodies in SUPERSEDED_BUILTIN_BODIES.items():
            db.add(EmailTemplate(owner_id=OWNER, key=key, version=1, subject="cũ",
                                 body_text=sorted(bodies)[0], active=True))
        db.add(EmailTemplate(owner_id=OWNER, key="interview_reminder", version=9,
                             subject="Bản riêng", body_text="Nội dung do người dùng tự viết.",
                             active=True))
    context = {"candidate_name": "A", "job_title": "B", "public_url": "u", "expires_at": "e",
               "duration_minutes": "60", "start_at": "s", "meeting_url": "m",
               "reschedule_url": "r", "deadline": "d"}
    with session_scope() as db:
        upgraded = render_template(db, OWNER, "scheduling_invitation", context)
        assert upgraded["template_version"] == BUILTIN_TEMPLATE_VERSION
        assert "Trân trọng" in upgraded["body"]

        # A version above the built-in is someone's own wording and must survive untouched.
        custom = render_template(db, OWNER, "interview_reminder", context)
        assert custom["body"] == "Nội dung do người dùng tự viết."


def test_invitation_email_names_the_job_and_reads_as_local_time():
    application = _application()
    response = client.post(f"/api/applications/{application['id']}/scheduling-invitations",
                           json={"timezone_name": "Asia/Ho_Chi_Minh"})
    assert response.status_code == 201
    with session_scope() as db:
        from app.models import OutboxEvent
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "scheduling_invitation",
        ).order_by(OutboxEvent.created_at.desc()))
    body = event.payload["body"]
    title = next(job["title"] for job in client.get("/api/dashboard").json()["jobs"]
                 if job["id"] == application["job_id"])
    # The v1 copy never told the candidate which role they were being invited for.
    assert title in event.payload["subject"]
    assert title in body
    assert "Phỏng vấn trực tuyến" in body
    # An ISO timestamp is not something a candidate should be asked to decode.
    assert re.search(r"hiệu lực đến \d{2}:\d{2} Th[^,]+, \d{2}/\d{2}/\d{4}", body)
    assert "T00:00" not in body and "+00:00" not in body


def test_public_scheduling_survives_a_calendar_provider_outage(monkeypatch):
    application = _application()
    invitation = client.post(f"/api/applications/{application['id']}/scheduling-invitations",
                             json={"timezone_name": "Asia/Ho_Chi_Minh"}).json()
    token = invitation["public_url"].split("schedule=")[1]

    def explode(*_args, **_kwargs):
        raise httpx.HTTPStatusError("403 Forbidden", request=None, response=None)

    monkeypatch.setattr(scheduling, "provider_busy", explode)
    response = client.get(f"/api/public/scheduling/{token}")
    # Before the guard this raised straight out of the route: the candidate got an opaque 500
    # and could not book at all just because the recruiter's calendar was unreachable.
    assert response.status_code == 200
    assert response.json()["slots"]
    with session_scope() as db:
        assert db.scalar(select(AuditLog).where(
            AuditLog.action == "PROVIDER_FREEBUSY_UNAVAILABLE")) is not None


def test_unhandled_error_still_answers_with_json_and_cors_headers():
    @app.get("/api/_regression_boom")
    def _boom():
        raise RuntimeError("boom")

    local = TestClient(app, raise_server_exceptions=False)
    response = local.get("/api/_regression_boom", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    # Without the CORS header the browser refuses to read the body and the frontend can only
    # report "Failed to fetch" - no status, no message, nothing to act on.
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_every_builtin_template_key_has_superseded_bodies_recorded():
    # A new built-in without its previous body listed would silently strand tenants on old copy.
    assert set(BUILTIN_TEMPLATES) == set(SUPERSEDED_BUILTIN_BODIES)
