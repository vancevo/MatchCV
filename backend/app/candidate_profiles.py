from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from .models import Application, CandidateProfile, CandidateResumeVersion, Job, ResumeVersionComparison


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    return normalized or None


def normalize_phone(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("84") and len(digits) in {11, 12}:
        digits = "0" + digits[2:]
    if len(digits) < 9 or len(digits) > 11:
        return None
    return digits


def candidate_phone(text: str) -> str:
    match = re.search(r"(?<!\d)(?:\+?84|0)[\d .()-]{8,13}(?!\d)", text)
    if not match:
        return ""
    return normalize_phone(match.group(0)) or ""


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w]+", "_", value.strip(), flags=re.UNICODE).strip("_")
    return (cleaned or "Ung_vien")[:120]


def version_filename(candidate_name: str, version_number: int, original_filename: str | None) -> str:
    suffix = Path(original_filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        suffix = ".txt"
    return f"{_safe_name(candidate_name)}_CV_v{version_number}{suffix}"


def resolve_candidate_profile(
    db,
    *,
    owner_id: str,
    name: str,
    email: str | None,
    phone: str | None,
) -> CandidateProfile:
    normalized_email = normalize_email(email)
    normalized_phone = normalize_phone(phone)
    by_email = db.scalar(select(CandidateProfile).where(
        CandidateProfile.owner_id == owner_id,
        CandidateProfile.normalized_email == normalized_email,
    )) if normalized_email else None
    by_phone = db.scalar(select(CandidateProfile).where(
        CandidateProfile.owner_id == owner_id,
        CandidateProfile.normalized_phone == normalized_phone,
    )) if normalized_phone else None
    if by_email and by_phone and by_email.id != by_phone.id:
        raise ValueError("Email và số điện thoại đang thuộc hai hồ sơ ứng viên khác nhau")
    profile = by_email or by_phone
    now = utcnow()
    if not profile:
        profile = CandidateProfile(
            owner_id=owner_id,
            full_name=name.strip() or "Ứng viên chưa xác định",
            email=(email or "").strip() or None,
            normalized_email=normalized_email,
            phone=(phone or "").strip() or None,
            normalized_phone=normalized_phone,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(profile)
        db.flush()
        return profile
    profile.full_name = name.strip() or profile.full_name
    if normalized_email and not profile.normalized_email:
        profile.email = (email or "").strip()
        profile.normalized_email = normalized_email
    if normalized_phone and not profile.normalized_phone:
        profile.phone = (phone or "").strip()
        profile.normalized_phone = normalized_phone
    profile.last_seen_at = now
    return profile


def create_resume_version(
    db,
    *,
    profile: CandidateProfile,
    storage_key: str,
    original_filename: str | None,
    file_size: int | None,
    checksum: str,
    extracted_text: str,
    extraction: dict | None = None,
) -> CandidateResumeVersion:
    latest = db.scalar(select(func.max(CandidateResumeVersion.version_number)).where(
        CandidateResumeVersion.candidate_profile_id == profile.id
    )) or 0
    number = int(latest) + 1
    version = CandidateResumeVersion(
        id=str(uuid4()),
        owner_id=profile.owner_id,
        candidate_profile_id=profile.id,
        version_number=number,
        original_filename=original_filename,
        version_filename=version_filename(profile.full_name, number, original_filename),
        storage_key=storage_key,
        file_size=file_size,
        checksum=checksum,
        extracted_text=extracted_text,
        extraction=extraction or {},
        extracted_at=utcnow() if extraction else None,
        submitted_at=utcnow(),
    )
    db.add(version)
    db.flush()
    if version.extraction:
        from .resume_comparison import refresh_resume_comparisons_around
        from .semantic_index import index_resume_version
        refresh_resume_comparisons_around(db, version)
        index_resume_version(db, version)
    return version


def resume_extraction_snapshot(
    *, name: str, email: str | None, phone: str | None, screening: dict | None,
) -> dict:
    """Freeze the structured extraction that belongs to one CV version."""
    value = screening or {}
    structured = value.get("candidate_profile")
    if not isinstance(structured, dict):
        evidence = value.get("evidence") if isinstance(value.get("evidence"), list) else []
        structured = {
            "skills": list(dict.fromkeys(
                str(item.get("requirement", "")).strip()
                for item in evidence if isinstance(item, dict) and item.get("matched")
                and str(item.get("requirement", "")).strip()
            )),
            "experience_years": value.get("experience_years", 0),
            "education": [],
            "summary": "",
            "extraction_source": value.get("screening_source", "rules"),
        }
    return {
        "candidate": {"name": name, "email": email or "", "phone": phone or ""},
        "profile": structured,
        "evidence": value.get("evidence", []) if isinstance(value.get("evidence"), list) else [],
        "screening_source": value.get("screening_source", structured.get("extraction_source", "rules")),
    }


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    normalized = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def version_change(current: CandidateResumeVersion, previous: CandidateResumeVersion | None) -> dict:
    current_words = len(current.extracted_text.split())
    if not previous:
        return {"kind": "INITIAL", "similarity_percent": None, "word_delta": current_words,
                "summary": f"Phiên bản đầu tiên · {current_words} từ"}
    previous_words = len(previous.extracted_text.split())
    similarity = round(SequenceMatcher(None, previous.extracted_text, current.extracted_text).ratio() * 100)
    delta = current_words - previous_words
    if current.checksum == previous.checksum:
        summary = "Nội dung giống phiên bản trước"
        kind = "SAME_CONTENT"
    else:
        direction = f"+{delta}" if delta >= 0 else str(delta)
        summary = f"Thay đổi nội dung · tương đồng {similarity}% · {direction} từ"
        kind = "CHANGED"
    return {"kind": kind, "similarity_percent": similarity, "word_delta": delta, "summary": summary}


def candidate_profile_dict(db, profile: CandidateProfile) -> dict:
    versions = list(db.scalars(select(CandidateResumeVersion).where(
        CandidateResumeVersion.candidate_profile_id == profile.id,
        CandidateResumeVersion.owner_id == profile.owner_id,
    ).order_by(CandidateResumeVersion.version_number)))
    applications = list(db.scalars(select(Application).where(
        Application.candidate_profile_id == profile.id,
        Application.owner_id == profile.owner_id,
    ).order_by(Application.created_at.desc())))
    job_ids = {item.job_id for item in applications}
    comparisons = list(db.scalars(select(ResumeVersionComparison).where(
        ResumeVersionComparison.candidate_profile_id == profile.id,
        ResumeVersionComparison.owner_id == profile.owner_id,
    )))
    comparisons_by_target = {item.to_version_id: item.id for item in comparisons}
    jobs = {item.id: item for item in db.scalars(select(Job).where(Job.id.in_(job_ids)))} if job_ids else {}
    applications_by_version: dict[str, list[dict]] = {}
    for item in applications:
        if not item.resume_version_id:
            continue
        applications_by_version.setdefault(item.resume_version_id, []).append({
            "id": item.id,
            "job_id": item.job_id,
            "job_title": jobs[item.job_id].title if item.job_id in jobs else "Vị trí đã xoá",
            "status": item.status,
            "submitted_at": _iso(item.created_at),
        })
    rendered = []
    previous = None
    for value in versions:
        rendered.append({
            "id": value.id,
            "version": value.version_number,
            "filename": value.version_filename,
            "original_filename": value.original_filename,
            "size": value.file_size,
            "checksum": value.checksum,
            "submitted_at": _iso(value.submitted_at),
            "extracted_at": _iso(value.extracted_at),
            "extraction": value.extraction or {},
            "change": version_change(value, previous),
            "comparison_id": comparisons_by_target.get(value.id),
            "applications": applications_by_version.get(value.id, []),
        })
        previous = value
    rendered.reverse()
    return {
        "id": profile.id,
        "name": profile.full_name,
        "email": profile.email or "",
        "phone": profile.phone or "",
        "first_seen_at": _iso(profile.first_seen_at),
        "last_seen_at": _iso(profile.last_seen_at),
        "resume_count": len(versions),
        "application_count": len(applications),
        "resume_versions": rendered,
    }
