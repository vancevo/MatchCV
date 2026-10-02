from datetime import datetime, timezone

import pytest

from app.candidate_search import (
    CandidateVersionMatch,
    SearchWeights,
    cosine_similarity,
    rank_candidate_versions,
)


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _match(profile, version, number, semantic, skills, years=4):
    return CandidateVersionMatch(
        candidate_profile_id=profile,
        resume_version_id=version,
        version_number=number,
        semantic_similarity=semantic,
        skill_ids=frozenset(skills),
        experience_years=years,
        submitted_at=NOW,
        evidence=({"quote": "Built an ERP with React", "version": number},),
    )


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0)
    with pytest.raises(ValueError):
        cosine_similarity([1], [1, 2])


def test_versions_are_grouped_into_one_candidate_and_best_version_wins():
    results = rank_candidate_versions([
        _match("candidate-a", "v1", 1, 0.4, {"react"}),
        _match("candidate-a", "v2", 2, 0.9, {"react", "typescript"}),
        _match("candidate-b", "b1", 1, 0.7, {"python"}),
    ], required_skill_ids={"react", "typescript"}, now=NOW)

    assert [item.candidate_profile_id for item in results] == ["candidate-a", "candidate-b"]
    assert results[0].matched_version_id == "v2"
    assert results[0].other_matching_version_ids == ("v1",)
    assert results[0].matched_required_skills == ("react", "typescript")
    assert results[1].missing_required_skills == ("react", "typescript")


def test_hard_filters_remove_candidates_that_do_not_satisfy_requirements():
    results = rank_candidate_versions([
        _match("candidate-a", "v1", 1, 0.9, {"react"}, years=2),
        _match("candidate-b", "v1", 1, 0.7, {"react", "typescript"}, years=4),
    ], required_skill_ids={"react", "typescript"}, minimum_experience=3,
        required_skills_are_hard_filter=True, minimum_experience_is_hard_filter=True, now=NOW)

    assert [item.candidate_profile_id for item in results] == ["candidate-b"]


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        SearchWeights(semantic=0.9)


def test_unused_business_signals_are_omitted_and_weights_are_renormalized():
    result = rank_candidate_versions([
        _match("candidate-a", "v1", 1, 0.8, {"react"}),
    ], now=NOW)[0]

    assert set(result.score_components) == {"semantic", "freshness"}
    assert set(result.applied_weights) == {"semantic", "freshness"}
    assert sum(result.applied_weights.values()) == pytest.approx(100, abs=0.01)
    assert "required_skills" not in result.score_components
    assert "preferred_skills" not in result.score_components
    assert "experience" not in result.score_components


def test_only_requested_business_signals_contribute_to_score():
    result = rank_candidate_versions([
        _match("candidate-a", "v1", 1, 0.8, {"react", "typescript"}, years=4),
    ], required_skill_ids={"react"}, preferred_skill_ids={"typescript"},
        minimum_experience=3, now=NOW)[0]

    assert set(result.score_components) == {
        "semantic", "required_skills", "preferred_skills", "experience", "freshness",
    }
    assert sum(result.applied_weights.values()) == pytest.approx(100, abs=0.01)


def test_required_skills_can_be_enforced_as_a_hard_filter():
    results = rank_candidate_versions([
        _match("candidate-a", "v1", 1, 0.99, {"react"}),
        _match("candidate-b", "v1", 1, 0.70, {"react", "typescript"}),
    ], required_skill_ids={"react", "typescript"}, required_skills_are_hard_filter=True,
        now=NOW)

    assert [item.candidate_profile_id for item in results] == ["candidate-b"]


def test_hybrid_retrieval_combines_dense_and_exact_term_signals():
    result = rank_candidate_versions([
        CandidateVersionMatch(
            candidate_profile_id="candidate-a", resume_version_id="v1", version_number=1,
            semantic_similarity=0.6, lexical_overlap=1.0, submitted_at=NOW,
        ),
    ], now=NOW)[0]

    assert result.retrieval_components == {
        "dense": 80.0,
        "lexical": 100.0,
        "hybrid": 84.0,
    }
    assert result.raw_lexical_overlap == 1.0


def test_lexical_weight_is_bounded():
    with pytest.raises(ValueError, match="lexical_weight"):
        rank_candidate_versions([], lexical_weight=1.1)
