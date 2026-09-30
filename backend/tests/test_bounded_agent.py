from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import AgentRun, CriteriaVersion, ScreeningArtifact


client = TestClient(app)


def _job(title: str) -> dict:
    response = client.post("/api/jobs", json={
        "title": title,
        "description": "Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm. Ưu tiên Docker.",
        "department": "Engineering",
        "location": "Remote",
    })
    assert response.status_code == 201
    return response.json()


def _application(job_id: str, name: str, text: str) -> dict:
    response = client.post("/api/applications", json={
        "job_id": job_id,
        "candidate_name": name,
        "candidate_email": f"{name.lower().replace(' ', '-')}@example.com",
        "resume_text": text,
    })
    assert response.status_code == 201
    return response.json()


def test_criteria_inbox_rescreen_lineage_and_trace():
    job = _job("Phase Two Lineage")
    pending = client.get("/api/approvals?request_type=CRITERIA").json()
    request = next(item for item in pending if item["job_id"] == job["id"])
    approved = client.post(f"/api/approvals/{request['id']}/resolve", json={
        "decision": "APPROVE", "note": "Initial criteria accepted",
    })
    assert approved.status_code == 200

    application = _application(
        job["id"], "Lineage Candidate",
        "4 năm Python FastAPI PostgreSQL REST API và triển khai Docker production.",
    )
    assert application["screening"]["calibration_version"] == "eval-v1"
    assert len(application["screening"].get("routing", {}).get("reasons", [])) == 0

    updated = client.put(f"/api/jobs/{job['id']}/criteria", json={
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "Redis"],
        "preferred_skills": ["Docker"],
        "minimum_experience": 3,
        "note": "Redis is now mandatory",
    })
    assert updated.status_code == 200
    assert updated.json()["criteria_version"]["version"] == 2
    request = next(item for item in client.get("/api/approvals?request_type=CRITERIA").json()
                   if item["job_id"] == job["id"])
    assert client.post(f"/api/approvals/{request['id']}/resolve", json={
        "decision": "APPROVE", "note": "Use v2",
    }).status_code == 200

    versions = client.get(f"/api/jobs/{job['id']}/criteria-versions").json()
    assert [item["version"] for item in versions] == [2, 1]
    with session_scope() as db:
        runs = list(db.scalars(select(AgentRun).where(
            AgentRun.application_id == application["id"]
        ).order_by(AgentRun.created_at)))
        assert len(runs) == 2
        assert runs[1].parent_run_id == runs[0].id
        assert runs[1].criteria_version_id != runs[0].criteria_version_id
        assert len(list(db.scalars(select(ScreeningArtifact).where(
            ScreeningArtifact.application_id == application["id"]
        )))) == 2
        run_id = runs[1].id
    trace = client.get(f"/api/agent-runs/{run_id}").json()
    assert trace["trace"]["calibration_version"] == "eval-v1"
    assert "hash_embedding" in trace["trace"]["tools"]
    assert trace["usage"]["cost_micros"] == 0


def test_low_confidence_routes_to_inbox_and_clear_pass_skips_shortlist():
    job = _job("Phase Two Bounded")
    criteria_request = next(item for item in client.get("/api/approvals?request_type=CRITERIA").json()
                            if item["job_id"] == job["id"])
    client.post(f"/api/approvals/{criteria_request['id']}/resolve", json={"decision": "APPROVE"})
    strong = _application(job["id"], "Strong Bounded", "5 năm Python FastAPI PostgreSQL Docker REST API.")
    weak = _application(job["id"], "Weak Bounded", "2 năm hỗ trợ nội dung và chăm sóc khách hàng.")

    evidence = [item for item in client.get("/api/approvals?request_type=EVIDENCE").json()
                if item["application_id"] == weak["id"]]
    assert evidence
    assert "LOW_CONFIDENCE" in evidence[0]["payload"]["reasons"]

    # A CV that clears both the score and confidence bar is a clear yes: it stays WAITING_REVIEW
    # like everyone else, but it never enters the shortlist/Phê duyệt queue — that queue is only
    # for CVs that still need a closer human look.
    assert strong["status"] == "WAITING_REVIEW"
    shortlist = [item for item in client.get("/api/approvals?request_type=SHORTLIST").json()
                 if item["job_id"] == job["id"]]
    assert not any(strong["id"] in item["payload"].get("application_ids", []) for item in shortlist)

    reviewed = client.post(f"/api/applications/{strong['id']}/review", json={
        "decision": "INTERVIEW", "note": "Clear match, no committee review needed",
    })
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "INTERVIEW_PENDING"
