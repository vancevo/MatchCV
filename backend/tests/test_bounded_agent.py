from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import session_scope
from app.main import app
from app.models import AgentRun, CriteriaVersion, ScreeningArtifact, ShortlistProposal


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


def test_low_confidence_routes_to_inbox_and_shortlist_is_only_a_proposal():
    job = _job("Phase Two Bounded")
    configured = client.put(f"/api/jobs/{job['id']}/shortlist-trigger", json={
        "enabled": True, "min_completed": 2, "top_n": 1, "min_score": 65,
    })
    assert configured.status_code == 200
    criteria_request = next(item for item in client.get("/api/approvals?request_type=CRITERIA").json()
                            if item["job_id"] == job["id"])
    client.post(f"/api/approvals/{criteria_request['id']}/resolve", json={"decision": "APPROVE"})
    strong = _application(job["id"], "Strong Bounded", "5 năm Python FastAPI PostgreSQL Docker REST API.")
    weak = _application(job["id"], "Weak Bounded", "2 năm hỗ trợ nội dung và chăm sóc khách hàng.")

    evidence = [item for item in client.get("/api/approvals?request_type=EVIDENCE").json()
                if item["application_id"] == weak["id"]]
    assert evidence
    assert "LOW_CONFIDENCE" in evidence[0]["payload"]["reasons"]

    shortlist = [item for item in client.get("/api/approvals?request_type=SHORTLIST").json()
                 if item["job_id"] == job["id"]]
    assert len(shortlist) == 1
    assert shortlist[0]["payload"]["application_ids"][0] == strong["id"]
    assert strong["status"] == "WAITING_REVIEW"
    with session_scope() as db:
        proposal = db.get(ShortlistProposal, shortlist[0]["resource_id"])
        assert proposal.status == "PENDING"

    resolved = client.post(f"/api/approvals/{shortlist[0]['id']}/resolve", json={
        "decision": "APPROVE", "note": "Human approved",
    })
    assert resolved.status_code == 200
    applications = client.get(f"/api/applications?job_id={job['id']}").json()
    assert next(item for item in applications if item["id"] == strong["id"])["status"] == "INTERVIEW_PENDING"
