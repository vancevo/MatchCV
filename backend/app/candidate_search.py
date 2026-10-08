"""Pure ranking helpers for hybrid candidate search.

Persistence and HTTP concerns intentionally stay outside this module so SQLite
and pgvector repositories can share the same scoring contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence


SEARCH_SCORE_VERSION = "candidate-search.v2"
DEFAULT_LEXICAL_WEIGHT = 0.20


@dataclass(frozen=True)
class SearchWeights:
    semantic: float = 0.50
    required_skills: float = 0.25
    preferred_skills: float = 0.10
    experience: float = 0.10
    freshness: float = 0.05

    def __post_init__(self) -> None:
        values = (self.semantic, self.required_skills, self.preferred_skills, self.experience, self.freshness)
        if any(value < 0 for value in values) or not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ValueError("Search weights must be non-negative and sum to 1")


@dataclass(frozen=True)
class CandidateVersionMatch:
    candidate_profile_id: str
    resume_version_id: str
    version_number: int
    semantic_similarity: float
    lexical_overlap: float | None = None
    skill_ids: frozenset[str] = field(default_factory=frozenset)
    experience_years: float = 0.0
    submitted_at: datetime | None = None
    evidence: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    category: str | None = None


@dataclass(frozen=True)
class RankedCandidate:
    candidate_profile_id: str
    matched_version_id: str
    matched_version_number: int
    score: float
    raw_semantic_similarity: float
    raw_lexical_overlap: float | None
    retrieval_components: Mapping[str, float]
    score_components: Mapping[str, float]
    applied_weights: Mapping[str, float]
    matched_required_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    other_matching_version_ids: tuple[str, ...]
    evidence: tuple[Mapping[str, object], ...]
    pre_rerank_score: float | None = None
    reranker_score: float | None = None
    confidence: float | None = None
    matched_preferred_skills: tuple[str, ...] = ()
    category: str | None = None


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Vectors must be non-empty and have the same dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _coverage(actual: frozenset[str], expected: frozenset[str]) -> float:
    if not expected:
        raise ValueError("Coverage is undefined when no skills were requested")
    return len(actual & expected) / len(expected)


def _experience_score(actual: float, minimum: float | None) -> float:
    if minimum is None or minimum <= 0:
        return 1.0
    return min(1.0, max(0.0, actual) / minimum)


def _freshness_score(submitted_at: datetime | None, now: datetime) -> float:
    if submitted_at is None:
        return 0.0
    submitted = submitted_at.replace(tzinfo=submitted_at.tzinfo or timezone.utc)
    age_days = max(0.0, (now - submitted.astimezone(timezone.utc)).total_seconds() / 86400)
    return max(0.0, 1.0 - age_days / 730.0)


def rank_candidate_versions(
    matches: Iterable[CandidateVersionMatch],
    *,
    required_skill_ids: Iterable[str] = (),
    preferred_skill_ids: Iterable[str] = (),
    minimum_experience: float | None = None,
    required_skills_are_hard_filter: bool = False,
    minimum_experience_is_hard_filter: bool = False,
    weights: SearchWeights = SearchWeights(),
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    now: datetime | None = None,
    limit: int = 20,
    category: str | None = None,
    category_weight: float = 0.0,
    category_is_hard_filter: bool = False,
) -> list[RankedCandidate]:
    if not 0 <= lexical_weight <= 1:
        raise ValueError("lexical_weight must be between 0 and 1")
    required = frozenset(required_skill_ids)
    preferred = frozenset(preferred_skill_ids)
    current = now or datetime.now(timezone.utc)
    active_weights = {
        "semantic": weights.semantic,
        "freshness": weights.freshness,
    }
    if required:
        active_weights["required_skills"] = weights.required_skills
    if preferred:
        active_weights["preferred_skills"] = weights.preferred_skills
    if minimum_experience is not None and minimum_experience > 0:
        active_weights["experience"] = weights.experience
    if category and category_weight > 0:
        active_weights["category"] = category_weight
    active_total = sum(active_weights.values())
    normalized_weights = {
        name: value / active_total for name, value in active_weights.items()
    }
    scored: list[tuple[CandidateVersionMatch, float, dict[str, float]]] = []
    for item in matches:
        if required_skills_are_hard_filter and not required.issubset(item.skill_ids):
            continue
        if minimum_experience_is_hard_filter and minimum_experience is not None and item.experience_years < minimum_experience:
            continue
        if category_is_hard_filter and category and (item.category or "UNCLASSIFIED") != category:
            continue
        # Cosine is [-1, 1]; ranking components use a stable [0, 1] range.
        dense = min(1.0, max(0.0, (item.semantic_similarity + 1.0) / 2.0))
        lexical = None if item.lexical_overlap is None else min(1.0, max(0.0, item.lexical_overlap))
        semantic = dense if lexical is None else dense * (1 - lexical_weight) + lexical * lexical_weight
        all_components = {
            "semantic": semantic,
            "freshness": _freshness_score(item.submitted_at, current),
        }
        if required:
            all_components["required_skills"] = _coverage(item.skill_ids, required)
        if preferred:
            all_components["preferred_skills"] = _coverage(item.skill_ids, preferred)
        if "experience" in normalized_weights:
            all_components["experience"] = _experience_score(item.experience_years, minimum_experience)
        if "category" in normalized_weights:
            # A CV filed in no category is neither a match nor a miss.
            all_components["category"] = 0.5 if item.category is None else float(item.category == category)
        components = {name: all_components[name] for name in normalized_weights}
        score = sum(components[name] * normalized_weights[name] for name in components)
        scored.append((item, score, components))

    by_profile: dict[str, list[tuple[CandidateVersionMatch, float, dict[str, float]]]] = {}
    for value in scored:
        by_profile.setdefault(value[0].candidate_profile_id, []).append(value)

    results: list[RankedCandidate] = []
    for profile_id, versions in by_profile.items():
        versions.sort(key=lambda value: (value[1], value[0].version_number), reverse=True)
        best, score, components = versions[0]
        results.append(RankedCandidate(
            candidate_profile_id=profile_id,
            matched_version_id=best.resume_version_id,
            matched_version_number=best.version_number,
            score=round(score * 100, 2),
            raw_semantic_similarity=round(best.semantic_similarity, 6),
            raw_lexical_overlap=(
                round(best.lexical_overlap, 6) if best.lexical_overlap is not None else None
            ),
            retrieval_components={
                "dense": round(min(1.0, max(0.0, (best.semantic_similarity + 1.0) / 2.0)) * 100, 2),
                **({"lexical": round(best.lexical_overlap * 100, 2)}
                   if best.lexical_overlap is not None else {}),
                "hybrid": round(components["semantic"] * 100, 2),
            },
            score_components={key: round(value * 100, 2) for key, value in components.items()},
            applied_weights={key: round(value * 100, 2) for key, value in normalized_weights.items()},
            matched_required_skills=tuple(sorted(best.skill_ids & required)),
            missing_required_skills=tuple(sorted(required - best.skill_ids)),
            matched_preferred_skills=tuple(sorted(best.skill_ids & preferred)),
            category=best.category,
            other_matching_version_ids=tuple(value[0].resume_version_id for value in versions[1:]),
            evidence=best.evidence,
        ))
    results.sort(key=lambda item: (item.score, item.matched_version_number), reverse=True)
    return results[:max(0, limit)]


def apply_reranker_scores(
    ranked: Sequence[RankedCandidate],
    scores: Sequence[float],
    *,
    weight: float,
) -> list[RankedCandidate]:
    if len(ranked) != len(scores):
        raise ValueError("Reranker scores must align with ranked candidates")
    if not 0 <= weight <= 1:
        raise ValueError("reranker weight must be between 0 and 1")
    values = [replace(
        item,
        pre_rerank_score=item.score,
        reranker_score=round(min(1.0, max(0.0, float(score))) * 100, 2),
        score=round(((item.score / 100) * (1 - weight) + min(1.0, max(0.0, float(score))) * weight) * 100, 2),
    ) for item, score in zip(ranked, scores)]
    values.sort(key=lambda item: (item.score, item.matched_version_number), reverse=True)
    return values


def apply_calibrated_confidence(
    ranked: Sequence[RankedCandidate],
    parameters: Mapping[str, float],
) -> list[RankedCandidate]:
    from .candidate_search_calibration import calibrated_probability

    return [replace(
        item,
        confidence=round(calibrated_probability(item.score, dict(parameters)), 4),
    ) for item in ranked]
