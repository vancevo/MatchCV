from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


CV_TEXT = """Nguyễn Văn Python
python@example.com
Backend Engineer với 4 năm kinh nghiệm.
Xây dựng REST API bằng Python, FastAPI và PostgreSQL.
Triển khai Docker và Redis trên AWS.
"""


def test_upload_search_and_read_cv() -> None:
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/cvs",
            files={"file": ("nguyen-van-python.txt", CV_TEXT.encode(), "text/plain")},
            data={"source": "TEST", "location": "Ho Chi Minh"},
        )
        assert uploaded.status_code == 201, uploaded.text
        cv = uploaded.json()
        assert cv["specialization"] == "BACKEND"
        assert "Python" in cv["skills"]

        found = client.post(
            "/api/v1/cvs/search",
            headers={"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"},
            json={
                "query": "Backend Python REST API",
                "filters": {"required_skills": ["Python"], "minimum_experience": 3},
                "limit": 10,
            },
        )
        assert found.status_code == 200, found.text
        body = found.json()
        assert body["mode"] == "KEYWORD_FALLBACK"
        assert body["results"][0]["id"] == cv["id"]
        assert body["results"][0]["evidence"]

        detail = client.get(
            f"/api/v1/cvs/{cv['id']}",
            headers={"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"},
        )
        assert detail.status_code == 200
        assert "FastAPI" in detail.json()["extracted_text"]


def test_duplicate_upload_returns_same_cv() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/cvs",
            files={"file": ("cv-one.txt", CV_TEXT.encode(), "text/plain")},
        )
        second = client.post(
            "/api/v1/cvs",
            files={"file": ("cv-copy.txt", CV_TEXT.encode(), "text/plain")},
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]


def test_service_key_cannot_modify_cv() -> None:
    with TestClient(app) as client:
        listed = client.get(
            "/api/v1/cvs",
            headers={"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"},
        )
        cv_id = listed.json()["items"][0]["id"]
        response = client.patch(
            f"/api/v1/cvs/{cv_id}",
            headers={"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"},
            json={"full_name": "Không được sửa"},
        )
        assert response.status_code == 403


def test_semantic_search_path_backfills_chunk_embeddings(monkeypatch) -> None:
    def fake_embed(values: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "python" in value.casefold() or "backend" in value.casefold() else [0.0, 1.0]
                for value in values]

    monkeypatch.setattr(main_module, "embed", fake_embed)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cvs/search",
            headers={"X-API-Key": "test-service-key", "X-Tenant-ID": "local-tenant"},
            json={"query": "Python backend", "filters": {}, "limit": 10},
        )
        assert response.status_code == 200, response.text
        assert response.json()["mode"] == "HYBRID_SEMANTIC"
        assert response.json()["results"][0]["semantic_score"] > 0.9


def test_bulk_upload_is_limited_to_twenty_files() -> None:
    with TestClient(app) as client:
        files = [
            ("files", (f"cv-{index}.txt", f"Candidate {index}\n3 năm Python FastAPI PostgreSQL".encode(), "text/plain"))
            for index in range(21)
        ]
        response = client.post("/api/v1/cvs/bulk-upload", files=files)
        assert response.status_code == 422
        assert response.json()["detail"] == "Mỗi batch tối đa 20 CV"
