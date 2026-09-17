"""Clearing a candidate out of the list must be undoable, and must leave a trail."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import AuditLog


client = TestClient(app)
CV = "6 năm Python FastAPI PostgreSQL Docker Redis REST API và vận hành production."


def _application(name: str = "Ứng Viên Xoá Thử") -> dict:
    return client.post("/api/applications", json={
        "job_id": "job-backend-01", "candidate_name": name,
        "candidate_email": f"{abs(hash(name)) % 10**8}@example.com", "resume_text": CV,
    }).json()


def test_delete_then_restore_puts_the_candidate_back_where_they_were():
    item = _application("Khôi Phục Được")
    client.post(f"/api/applications/{item['id']}/review", json={"decision": "MANUAL_REVIEW", "note": "xem thêm"})

    deleted = client.post(f"/api/applications/{item['id']}/delete").json()
    assert deleted["status"] == "DELETED"
    assert deleted["deleted_at"] and deleted["deleted_at"].endswith("Z")

    restored = client.post(f"/api/applications/{item['id']}/restore").json()
    # Not a generic bucket: they go back to the status they held before being cleared out.
    assert restored["status"] == "REVIEWED"
    assert restored["deleted_at"] is None


def test_deleting_twice_is_harmless():
    item = _application("Xoá Hai Lần")
    first = client.post(f"/api/applications/{item['id']}/delete").json()
    second = client.post(f"/api/applications/{item['id']}/delete").json()
    # The second call must not overwrite previous_status with DELETED, or restore is stuck.
    assert second["status"] == "DELETED"
    assert client.post(f"/api/applications/{item['id']}/restore").json()["status"] == "WAITING_REVIEW"
    assert first["deleted_at"]


def test_restoring_something_that_was_never_deleted_is_refused():
    item = _application("Chưa Xoá")
    assert client.post(f"/api/applications/{item['id']}/restore").status_code == 409


def test_both_actions_are_written_to_the_audit_trail():
    item = _application("Có Dấu Vết")
    client.post(f"/api/applications/{item['id']}/delete")
    client.post(f"/api/applications/{item['id']}/restore")
    with session_scope() as db:
        actions = [row.action for row in db.scalars(
            select(AuditLog).where(AuditLog.application_id == item["id"]))]
    assert "CANDIDATE_DELETED" in actions and "CANDIDATE_RESTORED" in actions


def test_a_deleted_candidate_is_not_ranked_into_the_shortlist():
    item = _application("Không Được Lọt Shortlist")
    job = item["job_id"]
    assert any(row["id"] == item["id"] for row in client.get(f"/api/jobs/{job}/shortlist?limit=50").json()["items"])
    client.post(f"/api/applications/{item['id']}/delete")
    assert not any(row["id"] == item["id"]
                   for row in client.get(f"/api/jobs/{job}/shortlist?limit=50").json()["items"])


def test_a_review_stamps_when_and_by_whom():
    item = _application("Có Mốc Thời Gian")
    reviewed = client.post(f"/api/applications/{item['id']}/review",
                           json={"decision": "REJECT", "note": "không đạt"}).json()
    # Without this the list can only say what happened, never when.
    assert reviewed["status_changed_at"] and reviewed["status_changed_at"].endswith("Z")
