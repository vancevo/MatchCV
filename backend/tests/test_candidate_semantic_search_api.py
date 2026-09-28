from __future__ import annotations

from fastapi.testclient import TestClient

from app.embedding_service import EmbeddingRuntimeConfig, EmbeddingService, set_embedding_service
from app.main import app


client = TestClient(app)


class QueryAwareEncoder:
    def encode(self, texts, *, normalize_embeddings=True):
        vectors = []
        for text in texts:
            normalized = text.casefold()
            vectors.append([1.0, 0.0] if "react" in normalized else [0.0, 1.0])
        return vectors


def _service(*, enabled: bool = True) -> EmbeddingService:
    return EmbeddingService(
        EmbeddingRuntimeConfig(enabled=enabled, model_name="test/bge-m3", model_path="unused", dimension=2),
        encoder=QueryAwareEncoder(),
    )


def test_semantic_search_returns_one_candidate_with_the_best_cv_version():
    service = _service()
    set_embedding_service(service)
    try:
        email = "semantic-api-candidate@example.com"
        first = client.post("/api/applications", json={
            "job_id": "job-backend-01", "candidate_name": "Semantic Candidate",
            "candidate_email": email,
            "resume_text": "3 năm Python FastAPI REST API PostgreSQL Docker và Redis.",
        })
        second = client.post("/api/applications", json={
            "job_id": "job-backend-01", "candidate_name": "Semantic Candidate",
            "candidate_email": email,
            "resume_text": "5 năm Python FastAPI REST API PostgreSQL Docker Redis và React TypeScript.",
        })
        assert first.status_code == second.status_code == 201

        response = client.post("/api/candidate-profiles/search", json={
            "query": "React frontend", "filters": {"latest_cv_only": False}, "limit": 20,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "SEMANTIC"
        candidates = [item for item in body["results"] if item["candidate_profile"]["email"] == email]
        assert len(candidates) == 1
        assert candidates[0]["matched_version"]["version"] == 2
        assert candidates[0]["other_matching_versions"]
        assert candidates[0]["evidence"][0]["resume_version_id"] == candidates[0]["matched_version"]["id"]
    finally:
        set_embedding_service(None)


def test_disabled_embedding_uses_keyword_fallback_without_remote_call():
    set_embedding_service(_service(enabled=False))
    try:
        response = client.post("/api/candidate-profiles/search", json={
            "query": "Python", "filters": {"latest_cv_only": True}, "limit": 5,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "KEYWORD_FALLBACK"
        assert body["semantic_available"] is False
        assert body["warning"]
        assert body["results"]
    finally:
        set_embedding_service(None)
