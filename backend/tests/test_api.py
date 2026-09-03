from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_and_dashboard():
    assert client.get("/api/health").json()["status"] == "ok"
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["metrics"]["candidates"] >= 1


def test_end_to_end_screen_review_and_schedule():
    application = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": "Test Candidate",
        "candidate_email": "test@example.com",
        "resume_text": "3 năm Python. FastAPI REST API, PostgreSQL, Docker và Redis.",
    })
    assert application.status_code == 201
    body = application.json()
    assert body["screening"]["recommendation"] == "Strong Match"
    assert body["screening"]["interview_kit"]["questions"]

    kit = client.get(f"/api/applications/{body['id']}/interview-kit")
    assert kit.status_code == 200
    assert len(kit.json()["questions"]) >= 5

    reviewed = client.post(f"/api/applications/{body['id']}/review", json={"decision": "INTERVIEW", "note": "Good evidence"})
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "INTERVIEW_PENDING"

    slot = client.get("/api/interviewers/recruiter-1/available-slots").json()[0]["start_at"]
    booked = client.post(f"/api/applications/{body['id']}/interview", json={"slot": slot})
    assert booked.status_code == 201
    assert booked.json()["status"] == "SCHEDULED"


def test_batch_upload_extracts_all_without_storing_original():
    response = client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files=[
            ("files", ("nguyen-van-a.txt", "Nguyễn Văn A\na@example.com\n4 năm Python FastAPI REST API PostgreSQL Docker Redis experience".encode(), "text/plain")),
            ("files", ("tran-thi-b.txt", "Trần Thị B\nb@example.com\n3 năm Python FastAPI PostgreSQL và REST API".encode(), "text/plain")),
        ],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["completed"] == 2
    assert body["failed"] == 0
    assert len(body["items"]) == 2
    assert body["items"][0]["application"]["screening"]["final_score"] >= 80
    assert body["items"][0]["application"]["screening"]["interview_kit"]["rubric"]
    assert "resume_storage_key" not in body["items"][0]["application"]


def test_criteria_and_shortlist_approval():
    job = client.post("/api/jobs", json={
        "title": "Shortlist Backend",
        "description": "Yêu cầu Python, FastAPI, PostgreSQL. Ít nhất 2 năm kinh nghiệm. Ưu tiên Docker.",
        "department": "Engineering",
        "location": "Remote",
    }).json()
    criteria = client.post(f"/api/jobs/{job['id']}/approve-criteria", json={"approved": True, "note": "Looks right"})
    assert criteria.status_code == 200
    assert criteria.json()["requirements"]["approval"]["status"] == "APPROVED"

    first = client.post("/api/applications", json={
        "job_id": job["id"],
        "candidate_name": "Strong Shortlist",
        "candidate_email": "strong@example.com",
        "resume_text": "4 năm Python FastAPI PostgreSQL REST API Docker.",
    }).json()
    client.post("/api/applications", json={
        "job_id": job["id"],
        "candidate_name": "Review Shortlist",
        "candidate_email": "review@example.com",
        "resume_text": "2 năm Python và REST API.",
    })

    shortlist = client.get(f"/api/jobs/{job['id']}/shortlist?limit=1")
    assert shortlist.status_code == 200
    assert shortlist.json()["items"][0]["id"] == first["id"]

    approved = client.post(f"/api/jobs/{job['id']}/approve-shortlist", json={
        "application_ids": [first["id"]],
        "note": "Approve top candidate",
    })
    assert approved.status_code == 200
    assert approved.json()["items"][0]["status"] == "SHORTLISTED"

    report = client.get(f"/api/jobs/{job['id']}/shortlist-report")
    assert report.status_code == 200
    assert "text/markdown" in report.headers["content-type"]
    assert "Strong Shortlist" in report.text
    assert "Interview questions" in report.text
    assert "CV verification" in report.text


def test_batch_upload_reports_per_file_failure():
    response = client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files=[
            ("files", ("good.txt", b"Candidate Good\ngood@example.com\n3 years Python FastAPI PostgreSQL REST API", "text/plain")),
            ("files", ("bad.exe", b"not accepted", "application/octet-stream")),
        ],
    )
    assert response.status_code == 202
    assert response.json()["completed"] == 1
    assert response.json()["failed"] == 1
    assert response.json()["status"] == "PARTIAL"


def test_reject_archive_and_delete_job_rules():
    job = client.post("/api/jobs", json={
        "title": "Temporary QA",
        "description": "Yêu cầu Python và REST API. Ít nhất 1 năm kinh nghiệm.",
        "department": "Engineering",
        "location": "Remote",
    }).json()
    delete_empty = client.delete(f"/api/jobs/{job['id']}")
    assert delete_empty.status_code == 200

    job_with_candidate = client.post("/api/jobs", json={
        "title": "Temporary Backend",
        "description": "Yêu cầu Python và FastAPI. Ít nhất 1 năm kinh nghiệm.",
        "department": "Engineering",
        "location": "Remote",
    }).json()
    application = client.post("/api/applications", json={
        "job_id": job_with_candidate["id"],
        "candidate_name": "Delete Guard",
        "candidate_email": "guard@example.com",
        "resume_text": "2 năm Python và FastAPI.",
    }).json()

    blocked = client.delete(f"/api/jobs/{job_with_candidate['id']}")
    assert blocked.status_code == 409

    rejected = client.post(f"/api/applications/{application['id']}/review", json={"decision": "REJECT", "note": "Not a fit"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    allowed = client.delete(f"/api/jobs/{job_with_candidate['id']}")
    assert allowed.status_code == 200
    assert allowed.json()["removed_applications"] == 1

    archived = client.post("/api/applications", json={
        "job_id": "job-backend-01",
        "candidate_name": "Archive Test",
        "candidate_email": "archive@example.com",
        "resume_text": "2 năm Python và FastAPI với REST API.",
    }).json()
    response = client.post(f"/api/applications/{archived['id']}/review", json={"decision": "ARCHIVE", "note": "Keep for later"})
    assert response.status_code == 200
    assert response.json()["status"] == "ARCHIVED"
