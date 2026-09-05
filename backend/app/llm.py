"""OpenRouter integration with deterministic fallbacks and verified evidence."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import get_settings
from .pipeline import analyze_evidence, extract_requirements, generate_interview_kit, screen_candidate


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def llm_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": bool(settings.openrouter_api_key),
        "model": settings.openrouter_model,
    }


def _redact(text: str, candidate_name: str = "", candidate_email: str = "") -> str:
    """Remove common PII before a CV is sent to a free external endpoint."""
    value = text
    if candidate_name.strip():
        value = re.sub(re.escape(candidate_name.strip()), "[CANDIDATE_NAME]", value, flags=re.IGNORECASE)
    if candidate_email.strip():
        value = re.sub(re.escape(candidate_email.strip()), "[EMAIL]", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[EMAIL]", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<!\d)(?:\+?84|0)[\d .()-]{8,13}(?!\d)", "[PHONE]", value)
    return value


def _json_content(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if isinstance(content, dict):
        return content
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    return json.loads(content)


async def _complete(system: str, user: str, model_override: str | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        return None
    payload = {
        "model": model_override or settings.openrouter_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 2500,
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": settings.openrouter_site_url,
                    "X-Title": settings.openrouter_app_title,
                },
                json=payload,
            )
        return _json_content(response)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _requirements(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    required = value.get("required_skills")
    preferred = value.get("preferred_skills")
    years = value.get("minimum_experience")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return fallback
    if not isinstance(preferred, list) or not all(isinstance(item, str) for item in preferred):
        preferred = []
    try:
        minimum_experience = max(0, int(years or 0))
    except (TypeError, ValueError):
        minimum_experience = fallback["minimum_experience"]
    return {
        "required_skills": list(dict.fromkeys(item.strip() for item in required if item.strip())),
        "preferred_skills": list(dict.fromkeys(item.strip() for item in preferred if item.strip())),
        "minimum_experience": minimum_experience,
        "extraction_source": "openrouter",
    }


async def extract_requirements_ai(description: str) -> dict[str, Any]:
    fallback = {**extract_requirements(description), "extraction_source": "rules"}
    result = await _complete(
        """You extract hiring requirements from a job description. Return JSON only with:
required_skills (string array), preferred_skills (string array), minimum_experience (integer years).
Keep concrete skills and qualifications. Do not invent missing requirements. Vietnamese input is allowed.""",
        description,
    )
    return _requirements(result, fallback) if result else fallback


def _exact_quote(cv_text: str, quote: Any) -> str | None:
    if not isinstance(quote, str) or not quote.strip():
        return None
    quote = quote.strip()
    start = cv_text.casefold().find(quote.casefold())
    return cv_text[start : start + len(quote)] if start >= 0 else None


def _verified_evidence(cv_text: str, requirements: list[str], raw: Any) -> list[dict[str, Any]]:
    fallback = {item["requirement"].casefold(): item for item in analyze_evidence(cv_text, requirements)}
    items = raw if isinstance(raw, list) else []
    by_requirement = {
        str(item.get("requirement", "")).casefold(): item for item in items if isinstance(item, dict)
    }
    verified: list[dict[str, Any]] = []
    for requirement in requirements:
        item = by_requirement.get(requirement.casefold(), {})
        quote = _exact_quote(cv_text, item.get("evidence"))
        if item.get("matched") is True and quote:
            verified.append({
                "requirement": requirement,
                "matched": True,
                "evidence": quote,
                "confidence": min(1.0, max(0.0, float(item.get("confidence", 0.85)))),
                "source": "openrouter_verified_quote",
            })
        else:
            verified.append({**fallback[requirement.casefold()], "source": "rules"})
    return verified


def _profile(value: Any, cv_text: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    skills = value.get("skills", [])
    education = value.get("education", [])
    summary = value.get("summary", "")
    try:
        years = max(0.0, float(value.get("experience_years", 0)))
    except (TypeError, ValueError):
        years = 0.0
    return {
        "skills": [str(item).strip() for item in skills if str(item).strip()] if isinstance(skills, list) else [],
        "experience_years": years,
        "education": [str(item).strip() for item in education if str(item).strip()] if isinstance(education, list) else [],
        "summary": str(summary).strip()[:1000],
        "extraction_source": "openrouter",
        "source_characters": len(cv_text),
    }


async def screen_candidate_ai(
    cv_text: str, requirements: dict[str, Any], candidate_name: str = "", candidate_email: str = "",
    model_override: str | None = None, prompt_profile: str = "baseline",
) -> dict[str, Any]:
    baseline = screen_candidate(cv_text, requirements)
    all_requirements = requirements.get("required_skills", []) + requirements.get("preferred_skills", [])
    profile_instruction = {
        "baseline": "",
        "strict_evidence": " Treat ambiguous or paraphrased evidence as unmatched; only accept literal support.",
    }.get(prompt_profile, "")
    result = await _complete(
        """You extract a candidate profile and evidence for hiring requirements. Return JSON only:
{"candidate_profile":{"skills":[],"experience_years":0,"education":[],"summary":""},
"evidence":[{"requirement":"exact requirement supplied","matched":true,"evidence":"exact verbatim CV quote","confidence":0.0}]}.
Include every supplied requirement exactly once. Evidence must be an exact contiguous quote from the CV.
If no exact evidence exists, set matched=false and evidence="". Never infer protected traits or invent facts.""" + profile_instruction,
        json.dumps({"requirements": all_requirements, "cv_text": _redact(cv_text, candidate_name, candidate_email)}, ensure_ascii=False),
        model_override=model_override,
    )
    if not result:
        return {**baseline, "screening_source": "rules", "candidate_profile": None}
    evidence = _verified_evidence(cv_text, all_requirements, result.get("evidence"))
    return {
        **baseline,
        "evidence": evidence,
        "screening_source": "openrouter_with_verified_evidence",
        "candidate_profile": _profile(result.get("candidate_profile"), cv_text),
    }


def _interview_kit(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return fallback
    questions = value.get("questions")
    rubric = value.get("rubric")
    if not isinstance(questions, list) or not questions:
        return fallback
    normalized_questions = []
    for item in questions[:8]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        normalized_questions.append({
            "type": str(item.get("type", "Interview")).strip()[:80],
            "question": question[:500],
            "signal": str(item.get("signal", "")).strip()[:500],
        })
    normalized_rubric = []
    if isinstance(rubric, list):
        for item in rubric[:6]:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", "")).strip()
            if not criterion:
                continue
            try:
                weight = int(item.get("weight", 0))
            except (TypeError, ValueError):
                weight = 0
            normalized_rubric.append({"criterion": criterion[:160], "weight": max(0, min(100, weight))})
    if not normalized_questions:
        return fallback
    return {
        "summary": str(value.get("summary", fallback["summary"])).strip()[:700],
        "questions": normalized_questions,
        "rubric": normalized_rubric or fallback["rubric"],
        "source": "openrouter",
    }


async def generate_interview_kit_ai(
    cv_text: str,
    requirements: dict[str, Any],
    screening: dict[str, Any],
    job_title: str = "",
    candidate_name: str = "",
    candidate_email: str = "",
) -> dict[str, Any]:
    fallback = generate_interview_kit(cv_text, requirements, screening, job_title)
    result = await _complete(
        """You are a recruiter copilot. Create a structured interview kit from a CV screening result.
Return JSON only:
{"summary":"","questions":[{"type":"","question":"","signal":""}],"rubric":[{"criterion":"","weight":0}]}.
Create 5-7 concise Vietnamese questions. Include CV verification, technical depth, scenario, and gap probing.
Do not ask about age, gender, marital status, address, religion, ethnicity, health, or other protected traits.""",
        json.dumps({
            "job_title": job_title,
            "requirements": requirements,
            "screening": {
                "final_score": screening.get("final_score"),
                "recommendation": screening.get("recommendation"),
                "evidence": screening.get("evidence", []),
                "candidate_profile": screening.get("candidate_profile"),
            },
            "cv_text": _redact(cv_text, candidate_name, candidate_email),
        }, ensure_ascii=False),
    )
    return _interview_kit(result, fallback) if result else fallback
