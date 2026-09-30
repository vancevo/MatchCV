from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select

from .models import (
    CandidateResumeVersion,
    ResumeVersionComparison,
    ResumeVersionSkill,
    Skill,
    SkillAlias,
)


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    normalized = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def normalize_skill(value: str) -> str:
    """Normalize formatting without collapsing semantically different technologies."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    protected = {
        "c++": "c++",
        "c#": "c#",
        ".net": "dotnet",
        "asp.net": "aspdotnet",
        "node.js": "nodejs",
    }
    if text in protected:
        return protected[text]
    return re.sub(r"[\s._-]+", "", text)


def _skill_aliases(db) -> dict[str, tuple[Skill, str]]:
    result: dict[str, tuple[Skill, str]] = {}
    skills = {item.id: item for item in db.scalars(select(Skill))}
    for skill in skills.values():
        result[skill.normalized_name] = (skill, "CANONICAL")
    for alias in db.scalars(select(SkillAlias).where(SkillAlias.approved.is_(True))):
        skill = skills.get(alias.skill_id)
        if skill:
            result[alias.normalized_alias] = (skill, "ALIAS")
    return result


def canonicalize_skills(db, values: list[Any]) -> list[dict[str, Any]]:
    aliases = _skill_aliases(db)
    results: dict[str, dict[str, Any]] = {}
    for raw in values:
        raw_value = str(raw or "").strip()
        if not raw_value:
            continue
        normalized = normalize_skill(raw_value)
        match = aliases.get(normalized)
        if match:
            skill, method = match
            item = {
                "key": f"skill:{skill.id}",
                "skill_id": skill.id,
                "canonical_name": skill.canonical_name,
                "raw_value": raw_value,
                "match_method": method,
                "confidence": 1.0,
            }
        else:
            item = {
                "key": f"raw:{normalized}",
                "skill_id": None,
                "canonical_name": raw_value,
                "raw_value": raw_value,
                "match_method": "UNCLASSIFIED",
                "confidence": 0.0,
            }
        results.setdefault(item["key"], item)
    return list(results.values())


def sync_resume_version_skills(db, version: CandidateResumeVersion) -> list[dict[str, Any]]:
    profile = version.extraction.get("profile", {}) if isinstance(version.extraction, dict) else {}
    raw_skills = profile.get("skills", []) if isinstance(profile, dict) else []
    skills = canonicalize_skills(db, raw_skills if isinstance(raw_skills, list) else [])
    db.execute(delete(ResumeVersionSkill).where(ResumeVersionSkill.resume_version_id == version.id))
    for item in skills:
        db.add(ResumeVersionSkill(
            id=str(uuid4()),
            resume_version_id=version.id,
            skill_id=item["skill_id"],
            canonical_key=item["key"],
            canonical_name=item["canonical_name"],
            raw_value=item["raw_value"],
            match_method=item["match_method"],
            confidence=item["confidence"],
        ))
    db.flush()
    return skills


def _stable_value(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _list_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _stable_value(item))]


def _profile(version: CandidateResumeVersion) -> dict[str, Any]:
    extraction = version.extraction if isinstance(version.extraction, dict) else {}
    value = extraction.get("profile")
    return value if isinstance(value, dict) else {}


def _candidate(version: CandidateResumeVersion) -> dict[str, Any]:
    extraction = version.extraction if isinstance(version.extraction, dict) else {}
    value = extraction.get("candidate")
    return value if isinstance(value, dict) else {}


def _append_set_diff(
    category: str,
    before: list[str],
    after: list[str],
    added: list[dict],
    removed: list[dict],
    unchanged: list[dict],
) -> None:
    before_by_key = {item.casefold(): item for item in before}
    after_by_key = {item.casefold(): item for item in after}
    for key in sorted(after_by_key.keys() - before_by_key.keys()):
        added.append({"category": category, "value": after_by_key[key]})
    for key in sorted(before_by_key.keys() - after_by_key.keys()):
        removed.append({"category": category, "value": before_by_key[key]})
    for key in sorted(before_by_key.keys() & after_by_key.keys()):
        unchanged.append({"category": category, "value": after_by_key[key]})


def compare_resume_versions(db, previous: CandidateResumeVersion, current: CandidateResumeVersion) -> dict[str, list[dict]]:
    added: list[dict] = []
    removed: list[dict] = []
    modified: list[dict] = []
    unchanged: list[dict] = []
    conflicts: list[dict] = []

    previous_skills = {item["key"]: item for item in sync_resume_version_skills(db, previous)}
    current_skills = {item["key"]: item for item in sync_resume_version_skills(db, current)}
    for key in sorted(current_skills.keys() - previous_skills.keys()):
        item = current_skills[key]
        added.append({"category": "skills", "value": item["canonical_name"], "raw_value": item["raw_value"]})
    for key in sorted(previous_skills.keys() - current_skills.keys()):
        item = previous_skills[key]
        removed.append({"category": "skills", "value": item["canonical_name"], "raw_value": item["raw_value"]})
    for key in sorted(previous_skills.keys() & current_skills.keys()):
        before, after = previous_skills[key], current_skills[key]
        if before["raw_value"].casefold() != after["raw_value"].casefold():
            modified.append({
                "category": "skills",
                "before": before["raw_value"],
                "after": after["raw_value"],
                "canonical_value": after["canonical_name"],
                "change_type": "ALIAS_RENAMED",
            })
        else:
            unchanged.append({"category": "skills", "value": after["canonical_name"]})

    previous_profile, current_profile = _profile(previous), _profile(current)
    before_years = previous_profile.get("experience_years")
    after_years = current_profile.get("experience_years")
    if before_years == after_years:
        if before_years not in (None, ""):
            unchanged.append({"category": "experience_years", "value": before_years})
    elif before_years in (None, ""):
        added.append({"category": "experience_years", "value": after_years})
    elif after_years in (None, ""):
        removed.append({"category": "experience_years", "value": before_years})
    else:
        modified.append({"category": "experience_years", "before": before_years, "after": after_years,
                         "change_type": "VALUE_CHANGED"})
        try:
            if float(after_years) < float(before_years):
                conflicts.append({
                    "category": "experience_years",
                    "before": before_years,
                    "after": after_years,
                    "reason": "Số năm kinh nghiệm ở phiên bản sau thấp hơn phiên bản trước",
                })
        except (TypeError, ValueError):
            pass

    for category, key in (("education", "education"), ("projects", "projects"),
                          ("certificates", "certificates"), ("experiences", "experiences"),
                          ("languages", "languages")):
        before = _list_values(previous_profile.get(key))
        after = _list_values(current_profile.get(key))
        schema_before = previous_profile.get("resume_schema", {})
        schema_after = current_profile.get("resume_schema", {})
        if not before and isinstance(schema_before, dict):
            before = _list_values(schema_before.get(key))
        if not after and isinstance(schema_after, dict):
            after = _list_values(schema_after.get(key))
        _append_set_diff(category, before, after, added, removed, unchanged)

    for category, key in (("candidate_name", "name"), ("candidate_email", "email"), ("candidate_phone", "phone")):
        before = str(_candidate(previous).get(key) or "").strip()
        after = str(_candidate(current).get(key) or "").strip()
        if before == after:
            if before:
                unchanged.append({"category": category, "value": after})
        elif before and after:
            modified.append({"category": category, "before": before, "after": after,
                             "change_type": "VALUE_CHANGED"})
        elif after:
            added.append({"category": category, "value": after})
        elif before:
            removed.append({"category": category, "value": before})

    before_summary = str(previous_profile.get("summary") or "").strip()
    after_summary = str(current_profile.get("summary") or "").strip()
    if before_summary != after_summary:
        if before_summary and after_summary:
            modified.append({"category": "summary", "before": before_summary, "after": after_summary,
                             "change_type": "TEXT_CHANGED"})
        elif after_summary:
            added.append({"category": "summary", "value": after_summary})
        elif before_summary:
            removed.append({"category": "summary", "value": before_summary})
    elif before_summary:
        unchanged.append({"category": "summary", "value": before_summary})

    return {"added": added, "removed": removed, "modified": modified,
            "unchanged": unchanged, "conflicts": conflicts}


def ensure_resume_version_comparison(
    db,
    current: CandidateResumeVersion,
    *,
    rebuild: bool = False,
) -> ResumeVersionComparison | None:
    previous = db.scalar(select(CandidateResumeVersion).where(
        CandidateResumeVersion.candidate_profile_id == current.candidate_profile_id,
        CandidateResumeVersion.owner_id == current.owner_id,
        CandidateResumeVersion.version_number < current.version_number,
    ).order_by(CandidateResumeVersion.version_number.desc()))
    if not previous or not previous.extraction or not current.extraction:
        return None
    existing = db.scalar(select(ResumeVersionComparison).where(
        ResumeVersionComparison.from_version_id == previous.id,
        ResumeVersionComparison.to_version_id == current.id,
    ))
    if existing and not rebuild:
        return existing
    result = compare_resume_versions(db, previous, current)
    if existing:
        comparison = existing
    else:
        comparison = ResumeVersionComparison(
            id=str(uuid4()), owner_id=current.owner_id,
            candidate_profile_id=current.candidate_profile_id,
            from_version_id=previous.id, to_version_id=current.id,
        )
        db.add(comparison)
    comparison.added = result["added"]
    comparison.removed = result["removed"]
    comparison.modified = result["modified"]
    comparison.unchanged = result["unchanged"]
    comparison.conflicts = result["conflicts"]
    comparison.confidence = 1.0
    comparison.analysis_method = "STRUCTURED_DIFF+SKILL_TAXONOMY"
    comparison.model_name = None
    comparison.model_version = "cv-diff-v1"
    db.flush()
    return comparison


def refresh_resume_comparisons_around(db, version: CandidateResumeVersion) -> list[ResumeVersionComparison]:
    """Build both adjacent comparisons, including out-of-order background extraction."""
    results: list[ResumeVersionComparison] = []
    current = ensure_resume_version_comparison(db, version)
    if current:
        results.append(current)
    following = db.scalar(select(CandidateResumeVersion).where(
        CandidateResumeVersion.candidate_profile_id == version.candidate_profile_id,
        CandidateResumeVersion.owner_id == version.owner_id,
        CandidateResumeVersion.version_number > version.version_number,
    ).order_by(CandidateResumeVersion.version_number))
    if following and following.extraction:
        next_comparison = ensure_resume_version_comparison(db, following)
        if next_comparison:
            results.append(next_comparison)
    return results


def comparison_dict(
    comparison: ResumeVersionComparison,
    previous: CandidateResumeVersion,
    current: CandidateResumeVersion,
) -> dict[str, Any]:
    sections = {
        "added": comparison.added or [], "removed": comparison.removed or [],
        "modified": comparison.modified or [], "unchanged": comparison.unchanged or [],
        "conflicts": comparison.conflicts or [],
    }
    return {
        "id": comparison.id,
        "candidate_profile_id": comparison.candidate_profile_id,
        "from_version": {"id": previous.id, "version": previous.version_number,
                         "filename": previous.version_filename},
        "to_version": {"id": current.id, "version": current.version_number,
                       "filename": current.version_filename},
        **sections,
        "summary": {
            "added_count": len(sections["added"]),
            "removed_count": len(sections["removed"]),
            "modified_count": len(sections["modified"]),
            "unchanged_count": len(sections["unchanged"]),
            "conflict_count": len(sections["conflicts"]),
            "conflicts_count": len(sections["conflicts"]),
        },
        "confidence": comparison.confidence,
        "analysis_method": comparison.analysis_method,
        "model_name": comparison.model_name,
        "model_version": comparison.model_version,
        "created_at": _iso(comparison.created_at),
    }
