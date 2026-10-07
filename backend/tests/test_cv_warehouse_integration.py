from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.cv_warehouse_client import CvWarehouseClient
from app.database import session_scope
from app.main import app
from app.models import CandidateProfile


client = TestClient(app)


def test_talentflow_proxies_cv_warehouse_semantic_search(monkeypatch) -> None:
    async def fake_search(self, tenant_id: str, payload: dict) -> dict:
        assert tenant_id
        assert payload["query"] == "Python backend platform"
        return {
            "mode": "HYBRID_SEMANTIC",
            "embedding_model": "BAAI/bge-m3",
            "results": [{"id": "warehouse-cv-1", "full_name": "Warehouse Candidate", "ranking_score": 91.2}],
        }

    monkeypatch.setattr(CvWarehouseClient, "search", fake_search)
    response = client.post("/api/cv-warehouse/search", json={
        "query": "Python backend platform",
        "filters": {"required_skills": ["Python"]},
        "limit": 20,
    })
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "CV_WAREHOUSE"
    assert response.json()["mode"] == "HYBRID_SEMANTIC"


def test_talentflow_crawls_cv_into_job_and_deduplicates(monkeypatch) -> None:
    async def fake_get_cv(self, tenant_id: str, cv_id: str) -> dict:
        return {
            "id": cv_id,
            "full_name": "Ứng viên từ Kho CV",
            "email": "warehouse-crawl@example.com",
            "original_filename": "warehouse-candidate.txt",
            "file_size": 140,
            "checksum": "c" * 64,
            "updated_at": "2026-10-05T10:00:00Z",
            "extracted_text": (
                "Ứng viên từ Kho CV\nwarehouse-crawl@example.com\n"
                "4 năm Python FastAPI PostgreSQL REST API Docker và Redis."
            ),
        }

    monkeypatch.setattr(CvWarehouseClient, "get_cv", fake_get_cv)
    first = client.post("/api/jobs/job-backend-01/cv-warehouse/crawl", json={"cv_ids": ["warehouse-cv-1"]})
    assert first.status_code == 200, first.text
    assert first.json()["summary"]["imported"] == 1
    assert first.json()["summary"]["failed"] == 0

    second = client.post("/api/jobs/job-backend-01/cv-warehouse/crawl", json={"cv_ids": ["warehouse-cv-1"]})
    assert second.status_code == 200, second.text
    assert second.json()["summary"]["skipped"] == 1
    assert second.json()["skipped"][0]["reason"] == "DUPLICATE"


def test_import_from_warehouse_creates_profile_then_resume_version(monkeypatch) -> None:
    revision = {"value": 1}

    async def fake_list(self, tenant_id: str, *, query: str = "", limit: int = 100, offset: int = 0) -> dict:
        assert tenant_id
        return {"items": [{"id": "warehouse-profile-cv", "full_name": "Warehouse Profile"}],
                "limit": limit, "offset": offset}

    async def fake_get_cv(self, tenant_id: str, cv_id: str) -> dict:
        number = revision["value"]
        return {
            "id": cv_id, "full_name": "Warehouse Profile",
            "email": "warehouse-profile@example.com", "phone": "0901234567",
            "original_filename": f"warehouse-profile-v{number}.txt", "file_size": 140,
            "checksum": str(number) * 64, "updated_at": f"2026-10-0{number}T10:00:00Z",
            "skills": ["React", "TypeScript"], "experience_years": 4,
            "specialization": "FRONTEND", "job_title": "Frontend Developer",
            "extracted_text": (
                f"Warehouse Profile\nwarehouse-profile@example.com\n0901234567\n"
                f"CV revision {number}: 4 years React TypeScript."
            ),
        }

    async def fake_download(self, tenant_id: str, cv_id: str) -> bytes:
        return f"warehouse-file-{revision['value']}".encode()

    monkeypatch.setattr(CvWarehouseClient, "list_cvs", fake_list)
    monkeypatch.setattr(CvWarehouseClient, "get_cv", fake_get_cv)
    monkeypatch.setattr(CvWarehouseClient, "download_cv", fake_download)

    listed = client.get("/api/cv-warehouse/cvs")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["id"] == "warehouse-profile-cv"

    first = client.post("/api/cv-warehouse/import", json={"cv_ids": ["warehouse-profile-cv"]})
    assert first.status_code == 200, first.text
    assert first.json()["summary"]["new_profiles"] == 1
    profile_id = first.json()["imported"][0]["profile_id"]

    duplicate = client.post("/api/cv-warehouse/import", json={"cv_ids": ["warehouse-profile-cv"]})
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["skipped"][0]["reason"] == "DUPLICATE_CONTENT"

    async def fake_list_with_checksum(self, tenant_id: str, *, query: str = "", limit: int = 100, offset: int = 0) -> dict:
        return {"items": [{"id": "warehouse-profile-cv", "full_name": "Warehouse Profile",
                            "original_filename": "warehouse-profile-v1.txt", "checksum": "1" * 64}],
                "limit": limit, "offset": offset}

    monkeypatch.setattr(CvWarehouseClient, "list_cvs", fake_list_with_checksum)
    async def fail_if_existing_cv_is_fetched(self, tenant_id: str, cv_id: str) -> dict:
        raise AssertionError("CV đã có không được tải chi tiết lại")

    async def fail_if_existing_cv_is_downloaded(self, tenant_id: str, cv_id: str) -> bytes:
        raise AssertionError("CV đã có không được download lại")

    monkeypatch.setattr(CvWarehouseClient, "get_cv", fail_if_existing_cv_is_fetched)
    monkeypatch.setattr(CvWarehouseClient, "download_cv", fail_if_existing_cv_is_downloaded)
    synced = client.post("/api/cv-warehouse/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["summary"]["already_present"] == 1
    assert synced.json()["summary"]["imported"] == 0

    monkeypatch.setattr(CvWarehouseClient, "get_cv", fake_get_cv)
    monkeypatch.setattr(CvWarehouseClient, "download_cv", fake_download)
    revision["value"] = 2
    updated = client.post("/api/cv-warehouse/import", json={"cv_ids": ["warehouse-profile-cv"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["summary"]["new_versions"] == 1
    assert updated.json()["imported"][0]["profile_id"] == profile_id
    assert updated.json()["imported"][0]["version"] == 2


def test_import_reports_identity_split_instead_of_merging_profiles(monkeypatch) -> None:
    email = f"identity-{uuid4()}@example.com"
    phone = "0912345678"
    with session_scope() as db:
        db.add(CandidateProfile(
            full_name="Email Owner", owner_id="00000000-0000-0000-0000-000000000001", email=email,
            normalized_email=email, phone=None, normalized_phone=None,
        ))
        db.add(CandidateProfile(
            full_name="Phone Owner", owner_id="00000000-0000-0000-0000-000000000001", email=None,
            normalized_email=None, phone=phone, normalized_phone=phone,
        ))

    async def fake_get_cv(self, tenant_id: str, cv_id: str) -> dict:
        return {
            "id": cv_id, "full_name": "Conflicting Identity", "email": email, "phone": phone,
            "original_filename": "conflict.txt", "file_size": 100, "checksum": "f" * 64,
            "skills": ["Python"], "experience_years": 2,
            "extracted_text": f"Conflicting Identity\n{email}\n{phone}\n2 years Python experience.",
        }

    async def fake_download(self, tenant_id: str, cv_id: str) -> bytes:
        return b"conflicting warehouse cv"

    monkeypatch.setattr(CvWarehouseClient, "get_cv", fake_get_cv)
    monkeypatch.setattr(CvWarehouseClient, "download_cv", fake_download)
    response = client.post("/api/cv-warehouse/import", json={"cv_ids": ["identity-conflict-cv"]})
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["conflicts"] == 1
    assert response.json()["conflicts"][0]["type"] == "IDENTITY_SPLIT"
    assert response.json()["summary"]["imported"] == 0
