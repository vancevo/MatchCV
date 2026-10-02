from datetime import datetime, timezone

import pytest

from app.candidate_search import CandidateVersionMatch, apply_reranker_scores, rank_candidate_versions
from app.reranker_service import RerankerConfig, RerankerService, RerankerUnavailable


class FixedScorer:
    def score(self, pairs):
        assert pairs == [("python", "weak"), ("python", "strong")]
        return [-2.0, 1.4]


def _ranked():
    now = datetime.now(timezone.utc)
    return rank_candidate_versions([
        CandidateVersionMatch("first", "v1", 1, 0.8, submitted_at=now, evidence=({"text": "weak"},)),
        CandidateVersionMatch("second", "v2", 1, 0.6, submitted_at=now, evidence=({"text": "strong"},)),
    ], now=now)


def test_reranker_service_clamps_scores_and_blended_ranking_is_auditable():
    service = RerankerService(RerankerConfig(enabled=True), scorer=FixedScorer())
    scores = service.score_pairs("python", ["weak", "strong"])
    reranked = apply_reranker_scores(_ranked(), scores, weight=0.7)

    assert scores == [0.0, 1.0]
    assert reranked[0].candidate_profile_id == "second"
    assert reranked[0].pre_rerank_score is not None
    assert reranked[0].reranker_score == 100


def test_disabled_reranker_fails_open_at_the_service_boundary():
    service = RerankerService(RerankerConfig(enabled=False), scorer=FixedScorer())

    with pytest.raises(RerankerUnavailable, match="disabled"):
        service.score_pairs("query", ["document"])
