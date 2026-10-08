from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import ApprovalRequest

client = TestClient(app)


def _approved_job(title: str) -> dict:
    job = client.post("/api/jobs", json={
        "title": title, "description": "Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm.",
        "department": "Engineering", "location": "Remote",
    }).json()
    assert client.post(f"/api/jobs/{job['id']}/approve-criteria", json={"approved": True}).status_code == 200
    return job


def _application(job_id: str, email: str) -> dict:
    response = client.post("/api/applications", json={
        "job_id": job_id, "candidate_name": "Pool Candidate", "candidate_email": email,
        "resume_text": "4 năm Python FastAPI PostgreSQL REST API Docker. Tốt nghiệp đại học.",
    })
    assert response.status_code == 201
    return response.json()


def _pending_evidence(application_id: str, job_id: str) -> str:
    with session_scope() as db:
        request = ApprovalRequest(
            owner_id="00000000-0000-0000-0000-000000000001", request_type="EVIDENCE", job_id=job_id,
            application_id=application_id, resource_id=application_id, title="Kiểm tra evidence",
            summary="test", dedupe_key=f"test-evidence:{application_id}",
        )
        db.add(request); db.flush()
        return request.id


def test_rejecting_a_candidate_settles_their_pending_evidence_approval():
    job = _approved_job("Reject closes approval")
    application = _application(job["id"], "reject-closes@example.com")
    with session_scope() as db:
        for request in db.scalars(select(ApprovalRequest).where(ApprovalRequest.application_id == application["id"])):
            request.status = "APPROVED"
    approval_id = _pending_evidence(application["id"], job["id"])
    assert client.post(f"/api/applications/{application['id']}/review",
                       json={"decision": "REJECT", "note": "Không phù hợp"}).status_code == 200
    with session_scope() as db:
        request = db.get(ApprovalRequest, approval_id)
        assert request.status == "APPROVED"
        assert request.resolution["via"] == "application_review"


def test_manual_review_keeps_the_approval_pending():
    job = _approved_job("Manual review keeps approval")
    application = _application(job["id"], "manual-keeps@example.com")
    approval_id = _pending_evidence(application["id"], job["id"])
    assert client.post(f"/api/applications/{application['id']}/review", json={"decision": "MANUAL_REVIEW"}).status_code == 200
    with session_scope() as db:
        assert db.get(ApprovalRequest, approval_id).status == "PENDING"


def test_profile_can_be_put_into_another_job_once():
    source = _approved_job("Source job")
    target = _approved_job("Target job")
    application = _application(source["id"], "pool-candidate@example.com")
    profile_id = application["candidate_profile_id"]
    assert profile_id

    created = client.post(f"/api/candidate-profiles/{profile_id}/applications", json={"job_id": target["id"]})
    assert created.status_code == 201
    body = created.json()
    assert body["created"] is True
    assert body["application"]["job_id"] == target["id"]
    assert body["application"]["candidate_profile_id"] == profile_id
    assert body["application"]["screening"]["final_score"] > 0

    again = client.post(f"/api/candidate-profiles/{profile_id}/applications", json={"job_id": target["id"]})
    assert again.status_code == 201
    assert again.json()["created"] is False
    assert again.json()["application"]["id"] == body["application"]["id"]

    invited = client.post(f"/api/applications/{body['application']['id']}/review", json={"decision": "INTERVIEW"})
    assert invited.json()["status"] == "INTERVIEW_PENDING"


def test_profile_endpoint_validates_profile_and_job():
    job = _approved_job("Validation job")
    assert client.post("/api/candidate-profiles/missing/applications", json={"job_id": job["id"]}).status_code == 404
    application = _application(job["id"], "validation@example.com")
    unknown_job = client.post(f"/api/candidate-profiles/{application['candidate_profile_id']}/applications",
                              json={"job_id": "no-such-job"})
    assert unknown_job.status_code == 404
