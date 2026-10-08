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


def test_bulk_upload_is_limited_to_fifty_files() -> None:
    with TestClient(app) as client:
        files = [
            ("files", (f"cv-{index}.txt", f"Candidate {index}\n3 năm Python FastAPI PostgreSQL".encode(), "text/plain"))
            for index in range(51)
        ]
        response = client.post("/api/v1/cvs/bulk-upload", files=files)
        assert response.status_code == 422
        assert response.json()["detail"] == "Mỗi batch tối đa 50 CV"


def test_name_skips_avatar_initials() -> None:
    from app.extraction import extract_metadata

    text = "NH\nPhạm Ngọc Hương\nQA Intern\nHà Nội | a@b.com | Phone: +84 000 000 494"
    assert extract_metadata(text, "QA_494_EN.pdf")["full_name"] == "Phạm Ngọc Hương"
    assert extract_metadata("Dương Bảo Ngân\nQA Intern\na@b.com", "x.pdf")["full_name"] == "Dương Bảo Ngân"


def test_taxonomy_has_eleven_fixed_categories_and_classifies_titles() -> None:
    from app.taxonomy import CATEGORIES, classify, detect_for_query

    assert len(CATEGORIES) == 11
    assert classify("Frontend Intern", "") == "FRONTEND"
    assert classify("Mobile Developer", "Android Kotlin") == "MOBILE"
    assert classify("Cloud Security Engineer", "") == "CYBERSECURITY"
    assert classify("React / Python Full-stack Developer", "") == "FULLSTACK"
    assert classify("", "no relevant words here") == "UNCLASSIFIED"
    assert detect_for_query("AI ML Engineer") == "AI_ML"
    assert detect_for_query("Python developer") is None


def test_upload_is_filed_into_its_category_and_search_routes_by_query() -> None:
    with TestClient(app) as client:
        for name, title in (("An Nguyen", "Data Engineer"), ("Binh Tran", "QA Automation Engineer")):
            text = f"{name}\n{title}\nHà Nội | {name.split()[0].lower()}@example.com\n3 năm kinh nghiệm Python SQL"
            created = client.post("/api/v1/cvs", files={"file": (f"{name}.txt", text.encode(), "text/plain")})
            assert created.status_code == 201
        assert created.json()["specialization"] == "QA_AUTOMATION"
        categories = {item["code"]: item["count"] for item in client.get("/api/v1/filters").json()["categories"]}
        assert categories["DATA_ENGINEERING"] >= 1 and categories["QA_AUTOMATION"] >= 1
        result = client.post("/api/v1/cvs/search", json={"query": "Data Engineer"}).json()
        # The category the query targets only boosts; it never hides the other categories.
        assert result["specialization_filter"] is None
        assert result["specialization_boost"]["code"] == "DATA_ENGINEERING"
        assert result["results"][0]["specialization"] == "DATA_ENGINEERING"
        pinned = client.post("/api/v1/cvs/search", json={"query": "Data Engineer", "filters": {"specialization": "DATA_ENGINEERING"}}).json()
        assert pinned["specialization_filter"]["source"] == "EXPLICIT"
        assert pinned["results"] and all(item["specialization"] == "DATA_ENGINEERING" for item in pinned["results"])
        assert client.post("/api/v1/cvs/search", json={"query": "x y", "filters": {"specialization": "nope"}}).status_code == 422
