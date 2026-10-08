"""OpenRouter integration with deterministic fallbacks and verified evidence."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import get_settings
from .skill_catalog import skill_key
from .pipeline import analyze_evidence, extract_requirements, generate_interview_kit, screen_candidate
from .talentflow_model import (
    candidate_profile_from_schema,
    extract_requirements_schema_ai,
    extract_resume_schema_ai,
    talentflow_config,
)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def llm_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": bool(settings.openrouter_api_key),
        "model": settings.openrouter_model,
        "talentflow": talentflow_config(),
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
    if not isinstance(content, str):
        # Some providers return content: null (e.g. a refusal, an internal error that still
        # answers 200, or a model that only emitted tool calls) - treat it the same as any other
        # malformed response rather than crashing with a raw AttributeError.
        raise ValueError("empty or non-string content in provider response")
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    return json.loads(content)


async def _complete(
    system: str, user: str, model_override: str | None = None,
    endpoint_url: str | None = None, endpoint_api_key: str | None = None,
) -> dict[str, Any] | None:
    """Posts to OpenRouter by default. `endpoint_url` points this at any other OpenAI-compatible
    chat-completions endpoint instead - e.g. an Ollama server tunneled out of a free Colab GPU
    runtime - reusing the exact same request/response shape and JSON parsing/fallback behavior."""
    settings = get_settings()
    if endpoint_url:
        url, api_key = endpoint_url, endpoint_api_key or ""
    else:
        url, api_key = OPENROUTER_URL, settings.openrouter_api_key
        if not api_key:
            return None
    payload = {
        "model": model_override or settings.openrouter_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 2500,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if not endpoint_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url
        headers["X-Title"] = settings.openrouter_app_title
    # A self-hosted endpoint (e.g. Ollama on a single shared GPU) may need to swap a different
    # model into VRAM before it can answer - when several "colab:" models are requested concurrently
    # and don't all fit in VRAM at once, that swap-per-request easily exceeds OpenRouter's normal
    # 45s budget, so self-hosted calls get a much longer allowance.
    timeout = 240 if endpoint_url else 45
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
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


def _dedupe_by_skill(*groups: list[str]) -> list[str]:
    """One entry per catalog skill, in first-seen order, spelled the way the last group that named it did.

    "Golang" from the model and "Go" from the rules are the same criterion; screening it twice doubled its weight.
    """
    spelling: dict[str, str] = {}
    order: list[str] = []
    for group in groups:
        for item in group:
            key = skill_key(item)
            if key not in spelling:
                order.append(key)
            spelling[key] = item
    return [spelling[key] for key in order]


def _merge_talentflow_requirements(result: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    required = _dedupe_by_skill(list(fallback.get("required_skills", [])), result.get("required_skills", []))
    required_keys = {skill_key(item) for item in required}
    preferred = _dedupe_by_skill(result.get("preferred_skills", []), list(fallback.get("preferred_skills", [])))
    preferred = [item for item in preferred if skill_key(item) not in required_keys]
    if "sql-optimization" in required_keys:
        preferred = [item for item in preferred if skill_key(item) != "sql"]
    minimum_experience = max(int(result.get("minimum_experience", 0) or 0), int(fallback.get("minimum_experience", 0) or 0))
    return {
        **{key: value for key, value in fallback.items() if key not in {"required_skills", "preferred_skills", "minimum_experience", "extraction_source"}},
        "required_skills": required,
        "preferred_skills": preferred,
        "minimum_experience": minimum_experience,
        "extraction_source": "talentflow_hf_with_rules_guardrails",
    }


async def extract_requirements_ai(description: str) -> dict[str, Any]:
    fallback = {**extract_requirements(description), "extraction_source": "rules"}
    talentflow_result = await extract_requirements_schema_ai(description)
    if talentflow_result:
        return _merge_talentflow_requirements(talentflow_result, fallback)
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
    resume_schema = await extract_resume_schema_ai(cv_text)
    if resume_schema:
        return {
            **baseline,
            "evidence": analyze_evidence(cv_text, all_requirements),
            "screening_source": "talentflow_hf_with_rules_evidence",
            "candidate_profile": candidate_profile_from_schema(resume_schema, cv_text),
        }
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


def _qa_pairs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []
    pairs = value.get("qa_pairs")
    if not isinstance(pairs, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in pairs:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question and not answer:
            continue
        normalized.append({
            "speaker_role": str(item.get("speaker_role", "")).strip()[:32],
            "question": question[:2000],
            "answer": answer[:4000],
        })
    return normalized


async def segment_transcript_ai(
    transcript_text: str, candidate_name: str = "", candidate_email: str = "",
) -> list[dict[str, str]]:
    """No deterministic fallback exists for segmenting free-form dialogue, so on failure this
    returns the whole transcript as a single untagged pair rather than nothing at all.

    Prefers the same Colab-hosted model used for the 3-model cross-analysis (Stage 2) over
    OpenRouter's free tier when configured. OpenRouter's free-tier daily quota (e.g. 50 req/day) is
    shared across every AI feature in this app and runs out easily - when it does, this call used
    to silently fail and every transcript degraded to "no speaker labels, one giant blob" with no
    visible error, which is confusing since Stage 2 itself keeps working fine via Colab."""
    settings = get_settings()
    endpoint_url = settings.colab_llm_endpoint_url or None
    model_override = settings.colab_llm_model if endpoint_url else None
    result = await _complete(
        """You are segmenting a job interview transcript (an interviewer or two plus the candidate,
no pre-existing speaker labels). Split it into an ordered list of interviewer-question /
candidate-answer pairs, inferring who is speaking from context. Return JSON only:
{"qa_pairs":[{"speaker_role":"Interviewer|Leader|HR|Candidate","question":"","answer":""}]}.
Keep question/answer text verbatim from the transcript, do not paraphrase.""",
        json.dumps({"transcript": _redact(transcript_text, candidate_name, candidate_email)}, ensure_ascii=False),
        model_override=model_override, endpoint_url=endpoint_url,
    )
    pairs = _qa_pairs(result) if result else []
    return pairs or [{"speaker_role": "", "question": "", "answer": transcript_text[:4000]}]


def _overall_assessment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    try:
        score = max(0, min(100, int(value.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    recommendation = value.get("recommendation")
    if recommendation not in {"STRONG_YES", "YES", "MIXED", "NO", "STRONG_NO"}:
        recommendation = "MIXED"
    return {"score": score, "recommendation": recommendation, "reasoning": str(value.get("reasoning", "")).strip()[:1000]}


def _verified_cross_analysis(
    value: Any, qa_pairs: list[dict[str, str]], known_claim_ids: set[str], known_requirements: set[str],
) -> dict[str, Any]:
    items = value.get("qa_analyses") if isinstance(value, dict) else None
    overall = _overall_assessment(value.get("overall_assessment") if isinstance(value, dict) else None)
    if not isinstance(items, list):
        return {"overall": overall, "qa_analyses": []}
    verified: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            qa_index = int(item.get("qa_index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= qa_index < len(qa_pairs)):
            continue
        answer_text = qa_pairs[qa_index].get("answer", "")
        quality = item.get("answer_quality") if isinstance(item.get("answer_quality"), dict) else {}

        linked_claims: list[dict[str, Any]] = []
        for claim in item.get("linked_claims", []) if isinstance(item.get("linked_claims"), list) else []:
            if not isinstance(claim, dict) or claim.get("claim_id") not in known_claim_ids:
                continue
            relationship = claim.get("relationship")
            if relationship not in {"VERIFIED", "NEW_INFO", "POTENTIAL_INCONSISTENCY", "NOT_ADDRESSED"}:
                continue
            quote = _exact_quote(answer_text, claim.get("evidence_quote"))
            # NOT_ADDRESSED legitimately has nothing to quote; every other relationship is a claim
            # about what was actually said, so it can't survive without a real quote backing it.
            if relationship != "NOT_ADDRESSED" and not quote:
                continue
            try:
                confidence = min(1.0, max(0.0, float(claim.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            linked_claims.append({
                "claim_id": claim["claim_id"], "relationship": relationship,
                "evidence_quote": quote or "", "confidence": confidence,
            })

        linked_requirements: list[dict[str, Any]] = []
        for req in item.get("linked_requirements", []) if isinstance(item.get("linked_requirements"), list) else []:
            if not isinstance(req, dict) or req.get("requirement") not in known_requirements:
                continue
            strength = req.get("evidence_strength")
            if strength not in {"STRONG", "WEAK", "NONE"}:
                continue
            quote = _exact_quote(answer_text, req.get("evidence_quote"))
            if strength != "NONE" and not quote:
                continue
            linked_requirements.append({
                "requirement": req["requirement"], "evidence_strength": strength,
                "evidence_quote": quote or "",
            })

        verified.append({
            "qa_index": qa_index,
            "answer_quality": {
                "relevance": str(quality.get("relevance", "")).strip()[:16],
                "completeness": str(quality.get("completeness", "")).strip()[:16],
                "specificity": str(quality.get("specificity", "")).strip()[:16],
                "has_example": bool(quality.get("has_example", False)),
            },
            "linked_claims": linked_claims,
            "linked_requirements": linked_requirements,
        })
    return {"overall": overall, "qa_analyses": verified}


async def cross_analyze_interview_ai(
    model: str, job_title: str, requirements: dict[str, Any],
    claims: list[dict[str, str]], qa_pairs: list[dict[str, str]],
) -> dict[str, Any] | None:
    """One specific model's cross-analysis of the whole interview in a single call. Returns None
    when the call itself fails - there is no sensible rule-based fallback for analyzing a
    free-form conversation, unlike CV screening's deterministic scorer.

    A `model` of the form "colab:<model-name>" routes to settings.colab_llm_endpoint_url (e.g. an
    Ollama server tunneled out of a free Colab GPU runtime) instead of OpenRouter - put one of
    these in INTERVIEW_ANALYSIS_MODELS alongside the OpenRouter slugs to add it to the fan-out."""
    known_claim_ids = {claim["id"] for claim in claims}
    known_requirements = {
        *requirements.get("required_skills", []), *requirements.get("preferred_skills", []),
    }
    endpoint_url: str | None = None
    actual_model = model
    if model.startswith("colab:"):
        settings = get_settings()
        endpoint_url = settings.colab_llm_endpoint_url
        if not endpoint_url:
            return None
        actual_model = model.split(":", 1)[1] or settings.colab_llm_model
    result = await _complete(
        """You are analyzing a job interview conversation (interviewer(s) + candidate). You are given
the candidate's CV claims (already verified against their CV) and the job's requirements.
For EACH question/answer pair, assess the answer's quality and link it to the CV claims and job
requirements it relates to, if any. A claim not supported by this answer is NOT evidence of lying -
use NEW_INFO (new, CV-unsupported info) or POTENTIAL_INCONSISTENCY (contradicts the CV), never a
truthfulness verdict.
Then form your own overall assessment of this candidate for this specific role: reason from how
they actually performed in the conversation - depth and specificity of answers, how much of the CV
and the job's requirements they verified or demonstrated firsthand, and your judgment of their
professional competence from how they explain their own work - not a mechanical average of the
per-answer labels above. Be decisive: a candidate with deep, specific, verified answers across the
requirements deserves a high score even if a couple of requirements went unasked.
Return JSON only:
{"overall_assessment":{"score":0,"recommendation":"STRONG_YES|YES|MIXED|NO|STRONG_NO","reasoning":""},
"qa_analyses":[{"qa_index":0,
"answer_quality":{"relevance":"high|medium|low","completeness":"high|medium|low","specificity":"high|medium|low","has_example":true},
"linked_claims":[{"claim_id":"","relationship":"VERIFIED|NEW_INFO|POTENTIAL_INCONSISTENCY|NOT_ADDRESSED","evidence_quote":"","confidence":0.0}],
"linked_requirements":[{"requirement":"","evidence_strength":"STRONG|WEAK|NONE","evidence_quote":""}]
}]}
score is 0-100, reasoning is 1-3 sentences in Vietnamese explaining the score.
evidence_quote must be an exact verbatim substring of that pair's answer text.""",
        json.dumps({
            "job_title": job_title, "requirements": requirements,
            "claims": claims, "qa_pairs": qa_pairs,
        }, ensure_ascii=False),
        model_override=actual_model, endpoint_url=endpoint_url,
    )
    if not result:
        return None
    return _verified_cross_analysis(result, qa_pairs, known_claim_ids, known_requirements)


async def transcribe_audio_ai(file_path: str) -> str | None:
    """Sends a recorded interview audio file to the Colab-hosted faster-whisper endpoint (a
    separate server/tunnel from the LLM one above - see colab_stt_endpoint_url) and returns the
    transcribed text. Returns None on any failure (not configured, network error, timeout,
    malformed response) - the caller turns that into a clear 503 asking for the manual-paste
    fallback instead, rather than ever crashing the request."""
    settings = get_settings()
    endpoint_url = settings.colab_stt_endpoint_url
    if not endpoint_url:
        return None
    try:
        with open(file_path, "rb") as audio_file:
            # Interview recordings can run long and a shared Colab GPU may need to warm up the
            # model first - a much longer budget than the LLM calls' own timeouts.
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(endpoint_url, files={"audio": audio_file})
            response.raise_for_status()
            text = response.json().get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None
    except (httpx.HTTPError, OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
