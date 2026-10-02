from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

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
        assert body["score_version"] == "candidate-search.v2"
        assert body["score_interpretation"] == "RANKING_ONLY"
        candidates = [item for item in body["results"] if item["candidate_profile"]["email"] == email]
        assert len(candidates) == 1
        assert candidates[0]["ranking_score"] == candidates[0]["score"]
        assert candidates[0]["result_id"]
        assert candidates[0]["confidence"] is None
        assert candidates[0]["retrieval"]["method"] == "HYBRID_BGE_M3_LEXICAL"
        assert set(candidates[0]["score_components"]) == {"semantic", "freshness"}
        assert sum(candidates[0]["applied_weights"].values()) == pytest.approx(100, abs=0.01)
        assert candidates[0]["matched_version"]["version"] == 2
        assert candidates[0]["other_matching_versions"]
        assert candidates[0]["evidence"][0]["resume_version_id"] == candidates[0]["matched_version"]["id"]

        feedback = client.post("/api/candidate-profiles/search/feedback", json={
            "result_id": candidates[0]["result_id"], "relevance": 3,
            "reason": "STRONG_MATCH", "note": "Relevant evidence",
        })
        assert feedback.status_code == 200
        assert feedback.json()["relevance"] == 3

        metrics = client.get("/api/candidate-profiles/search/metrics?days=30")
        assert metrics.status_code == 200
        assert metrics.json()["feedback"]["count"] >= 1
        assert metrics.json()["privacy"] == {
            "raw_query_stored": False, "raw_cv_stored_in_telemetry": False,
        }
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


def test_candidate_search_status_exposes_versioned_controls():
    response = client.get("/api/candidate-profiles/search/status")

    assert response.status_code == 200
    search = response.json()["search"]
    assert search["score_version"] == "candidate-search.v2"
    assert search["score_interpretation"] == "RANKING_ONLY"
    assert search["retrieval"] == "HYBRID_BGE_M3_LEXICAL"
    assert search["lexical_weight"] == pytest.approx(0.2)
    assert search["confidence_calibrated"] is False


def test_calibration_is_blocked_until_enough_human_judgments_exist():
    response = client.post("/api/candidate-profiles/search/calibration/rebuild")

    assert response.status_code == 409
    assert "at least 30 human judgments" in response.json()["detail"]
