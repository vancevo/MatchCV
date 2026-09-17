from __future__ import annotations

import json
import re
import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import get_settings


DEFAULT_SYSTEM_PROMPT = """You are TalentFlow Resume Extraction Engine.
Extract information only from the supplied resume.
Return exactly one valid JSON object and no explanation.
Do not infer facts that are not supported by the resume.
Use null for unknown scalar values and [] for unknown list values.
Preserve concrete technologies, dates, companies, education, projects, certificates, and languages when present."""

JD_SYSTEM_PROMPT = """You are TalentFlow Job Description Extraction Engine.
Extract hiring criteria only from the supplied job description.
Return exactly one valid JSON object and no explanation.
Do not infer requirements that are not supported by the job description.
Vietnamese input is allowed.
Classify mandatory requirements under required_skills and "lợi thế", "ưu tiên", "plus", "nice to have" requirements under preferred_skills."""

TOP_LEVEL_SCHEMA_FIELDS = (
    "about",
    "address",
    "certificates",
    "date_of_birth",
    "desired_position",
    "educations",
    "email",
    "employment_durations",
    "employment_types",
    "experiences",
    "first_name",
    "hobbies",
    "job_expectations",
    "job_experience",
    "languages",
    "last_name",
    "max_salary",
    "min_salary",
    "phone",
    "projects",
    "ready_to_relocation",
    "skills",
    "work_modes",
    "years_experience",
)

JD_SCHEMA_FIELDS = ("required_skills", "preferred_skills", "minimum_experience")


def talentflow_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "configured": bool(settings.talentflow_model_repo_id),
        "enabled": settings.talentflow_model_enabled,
        "repo_id": settings.talentflow_model_repo_id,
        "model_dir": settings.talentflow_model_dir,
    }


def build_messages(cv_text: str) -> list[dict[str, str]]:
    schema_stub = {
        field: [] if field in {
            "certificates",
            "educations",
            "employment_durations",
            "employment_types",
            "experiences",
            "hobbies",
            "languages",
            "projects",
            "skills",
            "work_modes",
        } else None
        for field in TOP_LEVEL_SCHEMA_FIELDS
    }
    return [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Required top-level schema:\n"
                + json.dumps(schema_stub, ensure_ascii=False)
                + "\n\nResume:\n"
                + cv_text
            ),
        },
    ]


def build_jd_messages(description: str) -> list[dict[str, str]]:
    schema_stub = {
        "required_skills": [],
        "preferred_skills": [],
        "minimum_experience": 0,
    }
    return [
        {"role": "system", "content": JD_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Required JSON schema:\n"
                + json.dumps(schema_stub, ensure_ascii=False)
                + "\n\nJob description:\n"
                + description
            ),
        },
    ]


def parse_json_object(text: str) -> dict[str, Any] | None:
    value = text.strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(value[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_resume_schema(value: dict[str, Any]) -> dict[str, Any] | None:
    hits = [field for field in TOP_LEVEL_SCHEMA_FIELDS if field in value]
    if len(hits) < 5:
        return None
    normalized = {field: value.get(field) for field in TOP_LEVEL_SCHEMA_FIELDS}
    return normalized


def normalize_requirements_schema(value: dict[str, Any]) -> dict[str, Any] | None:
    required = value.get("required_skills")
    preferred = value.get("preferred_skills", [])
    years = value.get("minimum_experience", 0)
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return None
    if not isinstance(preferred, list) or not all(isinstance(item, str) for item in preferred):
        return None
    try:
        minimum_experience = max(0, int(float(years or 0)))
    except (TypeError, ValueError):
        return None
    required_items = list(dict.fromkeys(item.strip() for item in required if item.strip()))
    preferred_items = list(dict.fromkeys(item.strip() for item in preferred if item.strip()))
    if not required_items and not preferred_items and minimum_experience == 0:
        return None
    return {
        "required_skills": required_items,
        "preferred_skills": preferred_items,
        "minimum_experience": minimum_experience,
        "extraction_source": "talentflow_hf",
    }


def candidate_profile_from_schema(schema: dict[str, Any], cv_text: str) -> dict[str, Any]:
    raw_skills = schema.get("skills")
    skills: list[str] = []
    if isinstance(raw_skills, list):
        for item in raw_skills:
            if isinstance(item, dict):
                name = item.get("skill_name")
            else:
                name = item
            if str(name or "").strip():
                skills.append(str(name).strip())

    educations = []
    for item in schema.get("educations") if isinstance(schema.get("educations"), list) else []:
        if not isinstance(item, dict):
            continue
        parts = [item.get("degree"), item.get("programme"), item.get("name")]
        text = " - ".join(str(part).strip() for part in parts if str(part or "").strip())
        if text:
            educations.append(text)

    years = schema.get("years_experience", schema.get("job_experience", 0))
    try:
        experience_years = max(0.0, float(years or 0))
    except (TypeError, ValueError):
        experience_years = 0.0

    summary = str(schema.get("about") or schema.get("job_expectations") or "").strip()
    if not summary:
        desired_position = str(schema.get("desired_position") or "").strip()
        summary = f"Candidate profile extracted for {desired_position}." if desired_position else ""

    return {
        "skills": list(dict.fromkeys(skills)),
        "experience_years": experience_years,
        "education": educations,
        "summary": summary[:1000],
        "extraction_source": "talentflow_hf",
        "source_characters": len(cv_text),
        "resume_schema_version": "talentflow.resume.v1",
        "resume_schema": schema,
    }


class TalentFlowResumeExtractor:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.model_dir = Path(settings.talentflow_model_dir)
        self.repo_id = settings.talentflow_model_repo_id
        self.token = settings.hf_token or None
        self._tokenizer = None
        self._model = None

    def _ensure_local_model(self) -> Path:
        if (self.model_dir / "config.json").exists() and (self.model_dir / "model.safetensors.index.json").exists():
            return self.model_dir
        if not self.repo_id:
            raise RuntimeError("TALENTFLOW_MODEL_REPO_ID is not configured")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError("huggingface_hub is required for TalentFlow model download") from exc
        self.model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=self.repo_id,
            repo_type="model",
            local_dir=str(self.model_dir),
            token=self.token,
            local_dir_use_symlinks=False,
        )
        return self.model_dir

    def _load(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        model_dir = self._ensure_local_model()
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("torch and transformers are required for TalentFlow local inference") from exc
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir),
            trust_remote_code=True,
            extra_special_tokens={},
        )
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        self._model = AutoModelForCausalLM.from_pretrained(
            str(model_dir),
            torch_dtype=dtype,
            device_map="auto" if torch.cuda.is_available() else None,
            trust_remote_code=True,
        )
        self._model.eval()

    def _generate_json(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        self._load()
        assert self._tokenizer is not None
        assert self._model is not None
        rendered = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(
            rendered,
            return_tensors="pt",
            truncation=True,
            max_length=self.settings.talentflow_max_input_tokens,
        )
        device = next(self._model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        output = self._model.generate(
            **inputs,
            max_new_tokens=self.settings.talentflow_max_new_tokens,
            do_sample=False,
            pad_token_id=self._tokenizer.eos_token_id,
        )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        return parse_json_object(text)

    def extract(self, cv_text: str) -> dict[str, Any] | None:
        parsed = self._generate_json(build_messages(cv_text))
        return normalize_resume_schema(parsed) if parsed else None

    def extract_requirements(self, description: str) -> dict[str, Any] | None:
        parsed = self._generate_json(build_jd_messages(description))
        return normalize_requirements_schema(parsed) if parsed else None


@lru_cache(maxsize=1)
def _extractor() -> TalentFlowResumeExtractor:
    return TalentFlowResumeExtractor()


async def extract_resume_schema_ai(cv_text: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.talentflow_model_enabled:
        return None
    if not settings.talentflow_model_repo_id and not Path(settings.talentflow_model_dir).exists():
        return None
    try:
        return await asyncio.to_thread(_extractor().extract, cv_text)
    except Exception:
        return None


async def extract_requirements_schema_ai(description: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.talentflow_model_enabled:
        return None
    if not settings.talentflow_model_repo_id and not Path(settings.talentflow_model_dir).exists():
        return None
    try:
        return await asyncio.to_thread(_extractor().extract_requirements, description)
    except Exception:
        return None
