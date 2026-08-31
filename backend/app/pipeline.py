"""Deterministic, explainable screening pipeline used by the demo and tests.

The interfaces are intentionally small so Docling, Ollama and embeddings can be
plugged in later without changing the API layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict


SKILL_ALIASES = {
    "postgresql": ("postgresql", "postgres", "psql"),
    "fastapi": ("fastapi",),
    "python": ("python",),
    "docker": ("docker", "container"),
    "redis": ("redis",),
    "rest api": ("rest api", "restful", "http api", "web service"),
}


@dataclass
class Evidence:
    requirement: str
    matched: bool
    evidence: str
    confidence: float


def extract_requirements(description: str) -> dict:
    text = description.lower()
    known = ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "REST API", "React", "TypeScript", "AWS"]
    skills = [skill for skill in known if skill.lower() in text]
    preferred_marker = text.find("ưu tiên")
    preferred = [s for s in skills if preferred_marker >= 0 and text.find(s.lower()) > preferred_marker]
    required = [s for s in skills if s not in preferred]
    years = re.search(r"(?:ít nhất|minimum|min\.?)\s*(\d+)\s*(?:năm|years?)", text)
    return {
        "required_skills": required or skills[:3],
        "preferred_skills": preferred,
        "minimum_experience": int(years.group(1)) if years else 0,
    }


def _aliases(skill: str) -> tuple[str, ...]:
    return SKILL_ALIASES.get(skill.lower(), (skill.lower(),))


def analyze_evidence(cv_text: str, requirements: list[str]) -> list[dict]:
    lines = [line.strip() for line in cv_text.splitlines() if line.strip()]
    lowered = [line.lower() for line in lines]
    evidence: list[Evidence] = []
    for requirement in requirements:
        aliases = _aliases(requirement)
        hit = next((lines[i] for i, line in enumerate(lowered) if any(a in line for a in aliases)), None)
        evidence.append(Evidence(
            requirement=requirement,
            matched=hit is not None,
            evidence=hit or "Không tìm thấy bằng chứng phù hợp trong CV.",
            confidence=0.94 if hit else 0.35,
        ))
    return [asdict(item) for item in evidence]


def screen_candidate(cv_text: str, requirements: dict) -> dict:
    required = requirements.get("required_skills", [])
    preferred = requirements.get("preferred_skills", [])
    all_evidence = analyze_evidence(cv_text, required + preferred)
    required_hits = sum(x["matched"] for x in all_evidence[: len(required)])
    preferred_hits = sum(x["matched"] for x in all_evidence[len(required) :])
    required_score = 100 * required_hits / max(len(required), 1)
    preferred_score = 100 * preferred_hits / max(len(preferred), 1) if preferred else 100

    year_values = [int(v) for v in re.findall(r"(\d+)\+?\s*(?:năm|years?)", cv_text.lower())]
    years = max(year_values, default=0)
    minimum = requirements.get("minimum_experience", 0)
    experience_score = 100 if minimum == 0 else min(100, years / minimum * 100)

    # Lightweight lexical semantic proxy for the offline MVP.
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z+#.]{1,}", cv_text.lower()))
    semantic_hits = sum(any(alias in cv_text.lower() for alias in _aliases(skill)) for skill in required)
    semantic_score = min(100, 35 + semantic_hits * 65 / max(len(required), 1) + min(len(tokens), 80) / 8)
    final = round(required_score * .40 + experience_score * .25 + semantic_score * .20 + preferred_score * .15, 1)
    recommendation = "Strong Match" if final >= 80 else "Potential Match" if final >= 65 else "Needs Review"
    return {
        "rule_score": round(required_score, 1),
        "experience_score": round(experience_score, 1),
        "semantic_score": round(semantic_score, 1),
        "preferred_score": round(preferred_score, 1),
        "final_score": final,
        "recommendation": recommendation,
        "evidence": all_evidence,
        "experience_years": years,
    }

