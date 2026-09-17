"""Candidate deletion is permanent and removes both workflow data and the original upload."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
CV_FILE = (
    "xoa-vinh-vien.txt",
    "LÊ VĂN XOÁ\nxoa@example.com\n5 năm Python FastAPI PostgreSQL REST API Docker Redis".encode(),
    "text/plain",
)


def _upload() -> dict:
    response = client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files=[("files", CV_FILE)],
    )
    assert response.status_code == 202
    return response.json()["items"][0]["application"]


def test_delete_removes_candidate_and_original_file_permanently():
    item = _upload()
    resume = client.get(f"/api/applications/{item['id']}/resume").json()
    assert resume["file_available"] is True
    local_path = Path("uploads") / f"{item['id']}.txt"
    assert local_path.exists()

    deleted = client.delete(f"/api/applications/{item['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert not local_path.exists()
    assert client.get(f"/api/applications/{item['id']}/resume").status_code == 404


def test_same_cv_can_be_submitted_again_after_permanent_delete():
    first = _upload()
    assert client.delete(f"/api/applications/{first['id']}").status_code == 200

    second = _upload()
    assert second["id"] == first["id"]
    assert second["status"] in {"PROCESSING", "WAITING_REVIEW"}


def test_identical_existing_cv_is_still_a_duplicate():
    _upload()
    repeat = client.post(
        "/api/application-batches",
        data={"job_id": "job-backend-01"},
        files=[("files", CV_FILE)],
    ).json()
    assert repeat["skipped"] == 1
