"""Build stable, PII-free documents for candidate semantic retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


TEMPLATE_VERSION = "candidate-search.v1"
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){8,15}(?!\w)")


@dataclass(frozen=True)
class CandidateSearchDocument:
    text: str
    content_hash: str
    template_version: str
    canonical_skills: tuple[str, ...]
    experience_years: float


def redact_pii(text: str, pii_values: Iterable[str] = ()) -> str:
    value = _EMAIL.sub(" ", text)
    value = _PHONE.sub(" ", value)
    for item in sorted({part.strip() for part in pii_values if part and part.strip()}, key=len, reverse=True):
        value = re.sub(re.escape(item), " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, Mapping):
            label = item.get("canonical_name") or item.get("skill_name") or item.get("name")
            if label and str(label).strip():
                result.append(str(label).strip())
    return result


def _records(value: Any, fields: tuple[str, ...]) -> list[str]:
    """Flatten selected structured fields without ever indexing contact details."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, Mapping):
            parts = [str(item.get(field) or "").strip() for field in fields]
            rendered = " - ".join(part for part in parts if part)
            if rendered:
                result.append(rendered)
    return result


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = unicodedata.normalize("NFKC", value).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _schema(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    value = profile.get("resume_schema")
    return value if isinstance(value, Mapping) else {}


def build_candidate_search_document(
    extraction: Mapping[str, Any] | None,
    *,
    canonical_skills: Iterable[str] = (),
    pii_values: Iterable[str] = (),
) -> CandidateSearchDocument:
    source = extraction or {}
    profile = source.get("profile") if isinstance(source.get("profile"), Mapping) else source
    assert isinstance(profile, Mapping)
    schema = _schema(profile)

    skills = _unique([*canonical_skills, *_strings(profile.get("skills")), *_strings(schema.get("skills"))])
    roles = _unique([
        *_strings(profile.get("roles")),
        *_strings(schema.get("desired_position")),
        *_records(schema.get("experiences"), ("job", "role", "position", "company", "description")),
    ])
    projects = _unique([*_strings(profile.get("projects")),
                        *_records(schema.get("projects"), ("name", "description", "technologies"))])
    education = _unique([*_strings(profile.get("education")),
                         *_records(schema.get("educations"), ("degree", "programme", "name"))])
    industries = _unique(_strings(profile.get("industries")) + _strings(profile.get("domains")))
    responsibilities = _unique(_strings(profile.get("responsibilities")))
    impact = _unique(_strings(profile.get("impact")) + _strings(profile.get("achievements")))
    summary = str(profile.get("summary") or schema.get("about") or "").strip()
    try:
        years = max(0.0, float(profile.get("experience_years", schema.get("years_experience", 0)) or 0))
    except (TypeError, ValueError):
        years = 0.0

    pii = [*pii_values]
    candidate = source.get("candidate")
    if isinstance(candidate, Mapping):
        pii.extend(str(candidate.get(key) or "") for key in ("name", "email", "phone"))

    sections = [
        ("roles", roles),
        ("canonical skills", skills),
        ("experience years", [f"{years:g}"] if years else []),
        ("industries", industries),
        ("responsibilities", responsibilities),
        ("projects", projects),
        ("impact", impact),
        ("education", education),
        ("professional summary", [summary] if summary else []),
    ]
    lines = []
    for label, values in sections:
        clean_values = [redact_pii(value, pii) for value in values]
        clean_values = [value for value in clean_values if value]
        if clean_values:
            lines.append(f"{label}: {'; '.join(clean_values)}")
    text = "\n".join(lines)
    payload = json.dumps(
        {"template": TEMPLATE_VERSION, "text": text}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    return CandidateSearchDocument(
        text=text,
        content_hash=hashlib.sha256(payload).hexdigest(),
        template_version=TEMPLATE_VERSION,
        canonical_skills=tuple(skills),
        experience_years=years,
    )
