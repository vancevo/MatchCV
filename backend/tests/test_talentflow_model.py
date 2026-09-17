from __future__ import annotations

import asyncio

from app.talentflow_model import (
    build_jd_messages,
    build_messages,
    candidate_profile_from_schema,
    normalize_requirements_schema,
    normalize_resume_schema,
    parse_json_object,
)


def test_talentflow_prompt_matches_training_shape():
    messages = build_messages("Nguyen Van A\nPython Developer")
    assert messages[0]["role"] == "system"
    assert "TalentFlow Resume Extraction Engine" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Required top-level schema" in messages[1]["content"]
    assert "Resume:" in messages[1]["content"]


def test_parse_and_map_talentflow_schema_to_candidate_profile():
    raw = """```json
{"first_name":"A","last_name":"Nguyen","about":"Backend engineer","skills":[{"skill_name":"Python"},{"skill_name":"FastAPI"}],"educations":[{"degree":"BS","programme":"Computer Science","name":"HCMUT"}],"years_experience":4,"experiences":[]}
```"""
    parsed = parse_json_object(raw)
    schema = normalize_resume_schema(parsed)
    profile = candidate_profile_from_schema(schema, "CV text")

    assert profile["extraction_source"] == "talentflow_hf"
    assert profile["skills"] == ["Python", "FastAPI"]
    assert profile["experience_years"] == 4
    assert profile["education"] == ["BS - Computer Science - HCMUT"]
    assert profile["resume_schema"]["first_name"] == "A"


def test_parse_and_normalize_talentflow_jd_requirements():
    messages = build_jd_messages("Có 3 năm kinh nghiệm Golang. Docker là lợi thế.")
    assert messages[0]["role"] == "system"
    assert "Job Description Extraction Engine" in messages[0]["content"]
    assert "required_skills" in messages[1]["content"]
    parsed = parse_json_object("""
    {"required_skills":["Golang"],"preferred_skills":["Docker"],"minimum_experience":3}
    """)
    requirements = normalize_requirements_schema(parsed)

    assert requirements == {
        "required_skills": ["Golang"],
        "preferred_skills": ["Docker"],
        "minimum_experience": 3,
        "extraction_source": "talentflow_hf",
    }


def test_extract_requirements_prefers_talentflow_jd_schema(monkeypatch):
    async def fake_requirements(_description: str):
        return {
            "required_skills": ["Golang", "SQL optimization"],
            "preferred_skills": ["Docker", "Web3"],
            "minimum_experience": 3,
            "extraction_source": "talentflow_hf",
        }

    async def openrouter_should_not_run(*_args, **_kwargs):
        raise AssertionError("OpenRouter should not run when TalentFlow JD extraction succeeds")

    monkeypatch.setattr("app.llm.extract_requirements_schema_ai", fake_requirements)
    monkeypatch.setattr("app.llm._complete", openrouter_should_not_run)

    from app.llm import extract_requirements_ai

    result = asyncio.run(extract_requirements_ai("Có 3 năm Golang, Docker là lợi thế."))

    assert result["extraction_source"] == "talentflow_hf_with_rules_guardrails"
    assert result["required_skills"] == ["Golang", "SQL optimization"]
    assert result["preferred_skills"] == ["Docker", "Web3"]
    assert result["minimum_experience"] == 3


def test_screen_candidate_prefers_talentflow_schema(monkeypatch):
    async def fake_schema(_cv_text: str):
        return normalize_resume_schema({
            "first_name": "A",
            "last_name": "Nguyen",
            "about": "Python backend engineer",
            "skills": [{"skill_name": "Python"}],
            "educations": [],
            "years_experience": 3,
        })

    monkeypatch.setattr("app.llm.extract_resume_schema_ai", fake_schema)

    from app.llm import screen_candidate_ai

    result = asyncio.run(screen_candidate_ai(
        "3 năm Python FastAPI.",
        {"required_skills": ["Python"], "preferred_skills": [], "minimum_experience": 2},
    ))

    assert result["screening_source"] == "talentflow_hf_with_rules_evidence"
    assert result["candidate_profile"]["extraction_source"] == "talentflow_hf"
    assert result["candidate_profile"]["skills"] == ["Python"]
