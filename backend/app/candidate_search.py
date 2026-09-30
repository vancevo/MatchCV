"""Pure ranking helpers for hybrid candidate search.

Persistence and HTTP concerns intentionally stay outside this module so SQLite
and pgvector repositories can share the same scoring contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence


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
    skill_ids: frozenset[str] = field(default_factory=frozenset)
    experience_years: float = 0.0
    submitted_at: datetime | None = None
    evidence: tuple[Mapping[str, object], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RankedCandidate:
    candidate_profile_id: str
    matched_version_id: str
    matched_version_number: int
    score: float
    score_components: Mapping[str, float]
    matched_required_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    other_matching_version_ids: tuple[str, ...]
    evidence: tuple[Mapping[str, object], ...]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Vectors must be non-empty and have the same dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _coverage(actual: frozenset[str], expected: frozenset[str]) -> float:
    return 1.0 if not expected else len(actual & expected) / len(expected)


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
    now: datetime | None = None,
    limit: int = 20,
) -> list[RankedCandidate]:
    required = frozenset(required_skill_ids)
    preferred = frozenset(preferred_skill_ids)
    current = now or datetime.now(timezone.utc)
    scored: list[tuple[CandidateVersionMatch, float, dict[str, float]]] = []
    for item in matches:
        if required_skills_are_hard_filter and not required.issubset(item.skill_ids):
            continue
        if minimum_experience_is_hard_filter and minimum_experience is not None and item.experience_years < minimum_experience:
            continue
        # Cosine is [-1, 1]; ranking components use a stable [0, 1] range.
        semantic = min(1.0, max(0.0, (item.semantic_similarity + 1.0) / 2.0))
        components = {
            "semantic": semantic,
            "required_skills": _coverage(item.skill_ids, required),
            "preferred_skills": _coverage(item.skill_ids, preferred),
            "experience": _experience_score(item.experience_years, minimum_experience),
            "freshness": _freshness_score(item.submitted_at, current),
        }
        score = sum(components[name] * getattr(weights, name) for name in components)
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
            score_components={key: round(value * 100, 2) for key, value in components.items()},
            matched_required_skills=tuple(sorted(best.skill_ids & required)),
            missing_required_skills=tuple(sorted(required - best.skill_ids)),
            other_matching_version_ids=tuple(value[0].resume_version_id for value in versions[1:]),
            evidence=best.evidence,
        ))
    results.sort(key=lambda item: (item.score, item.matched_version_number), reverse=True)
    return results[:max(0, limit)]
