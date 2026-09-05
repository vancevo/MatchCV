"""Bounded Phase-2 decisions: versioned criteria, calibrated screening and proposals."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from .models import (
    AgentRun,
    AgentTask,
    Application,
    ApprovalRequest,
    CriteriaVersion,
    Job,
    ScreeningArtifact,
    ShortlistProposal,
)
from .statuses import TaskStatus


EMBEDDING_DIMENSIONS = 96
CALIBRATION_VERSION = "eval-v1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def criteria_payload(requirements: dict) -> dict:
    return {
        "required_skills": list(requirements.get("required_skills", [])),
        "preferred_skills": list(requirements.get("preferred_skills", [])),
        "minimum_experience": max(0, int(requirements.get("minimum_experience", 0) or 0)),
    }


def criteria_text(criteria: dict) -> str:
    return " ".join([
        *criteria.get("required_skills", []),
        *criteria.get("preferred_skills", []),
        f"{criteria.get('minimum_experience', 0)} years experience",
    ])


def embed_text(text: str) -> list[float]:
    """Stable feature-hashing embedding: no network/model download required."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[\w+#.]{2,}", text.casefold(), flags=re.UNICODE)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        vector[bucket] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def calibrate_screening(result: dict, resume_text: str, criteria: dict) -> tuple[dict, list[float]]:
    embedding = embed_text(resume_text)
    similarity = max(-1.0, min(1.0, cosine(embedding, embed_text(criteria_text(criteria)))))
    vector_score = round((similarity + 1) * 50, 1)
    raw_score = float(result.get("final_score", 0))
    calibrated = round(max(0, min(100, raw_score * .95 + vector_score * .05)), 1)
    evidence = result.get("evidence", [])
    evidence_confidence = sum(float(item.get("confidence", 0)) for item in evidence) / max(len(evidence), 1)
    required = set(criteria.get("required_skills", []))
    required_evidence = [item for item in evidence if item.get("requirement") in required]
    required_coverage = sum(bool(item.get("matched")) for item in required_evidence) / max(len(required), 1)
    confidence = round(max(0, min(1, evidence_confidence * .65 + required_coverage * .35)), 3)
    reasons: list[str] = []
    if confidence < .65:
        reasons.append("LOW_CONFIDENCE")
    if 60 <= calibrated <= 75:
        reasons.append("BORDERLINE_SCORE")
    if raw_score >= 80 and required_coverage < .5:
        reasons.append("SCORE_EVIDENCE_MISMATCH")
    output = {
        **result,
        "raw_score": raw_score,
        "vector_score": vector_score,
        "calibrated_score": calibrated,
        "final_score": calibrated,
        "confidence": confidence,
        "calibration_version": CALIBRATION_VERSION,
        "routing": {"decision": "MANUAL_REVIEW" if reasons else "STANDARD_REVIEW", "reasons": reasons},
    }
    return output, embedding


def latest_criteria(db, job_id: str, *, approved_only: bool = False) -> CriteriaVersion | None:
    statement = select(CriteriaVersion).where(CriteriaVersion.job_id == job_id)
    if approved_only:
        statement = statement.where(CriteriaVersion.status == "APPROVED")
    return db.scalar(statement.order_by(CriteriaVersion.version.desc()).limit(1))


def ensure_criteria_version(db, job: Job) -> CriteriaVersion:
    existing = latest_criteria(db, job.id)
    if existing:
        return existing
    version = create_criteria_version(db, job, job.owner_id, job.requirements or {}, "Migrated current criteria")
    if (job.requirements or {}).get("approval", {}).get("status") == "APPROVED":
        version.status = "APPROVED"
        version.approved_at = utcnow()
        request = db.scalar(select(ApprovalRequest).where(ApprovalRequest.resource_id == version.id))
        if request:
            request.status = "APPROVED"
            request.decided_at = version.approved_at
            request.resolution = {"note": "Migrated approved criteria"}
    return version


def create_criteria_version(db, job: Job, owner_id: str, criteria: dict, note: str = "") -> CriteriaVersion:
    parent = latest_criteria(db, job.id)
    version = CriteriaVersion(
        owner_id=owner_id,
        job_id=job.id,
        version=(parent.version + 1) if parent else 1,
        criteria=criteria_payload(criteria),
        parent_id=parent.id if parent else None,
        change_note=note,
    )
    db.add(version)
    db.flush()
    add_approval(
        db,
        owner_id=owner_id,
        request_type="CRITERIA",
        job_id=job.id,
        resource_id=version.id,
        title=f"Duyệt tiêu chí v{version.version}: {job.title}",
        summary="Xác nhận yêu cầu trước khi agent dùng phiên bản này để rescreen.",
        payload={"version": version.version, "criteria": version.criteria, "change_note": note},
        dedupe_key=f"criteria:{version.id}",
    )
    return version


def add_approval(db, *, owner_id: str, request_type: str, resource_id: str, title: str,
                 summary: str, payload: dict, dedupe_key: str, job_id: str | None = None,
                 application_id: str | None = None) -> ApprovalRequest:
    existing = db.scalar(select(ApprovalRequest).where(ApprovalRequest.dedupe_key == dedupe_key))
    if existing:
        return existing
    value = ApprovalRequest(
        owner_id=owner_id,
        request_type=request_type,
        job_id=job_id,
        application_id=application_id,
        resource_id=resource_id,
        title=title,
        summary=summary,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    db.add(value)
    db.flush()
    return value


def record_screening_artifact(db, application: Application, run: AgentRun, result: dict,
                              embedding: list[float]) -> None:
    if not run.criteria_version_id:
        return
    db.add(ScreeningArtifact(
        owner_id=application.owner_id,
        application_id=application.id,
        run_id=run.id,
        criteria_version_id=run.criteria_version_id,
        result=result,
        embedding=embedding,
    ))
    reasons = result.get("routing", {}).get("reasons", [])
    if reasons:
        add_approval(
            db,
            owner_id=application.owner_id,
            request_type="EVIDENCE",
            job_id=application.job_id,
            application_id=application.id,
            resource_id=run.id,
            title=f"Kiểm tra evidence: {application.candidate_name}",
            summary="Agent không tự quyết định vì confidence/anomaly vượt ngưỡng policy.",
            payload={"run_id": run.id, "score": result.get("final_score"),
                     "confidence": result.get("confidence"), "reasons": reasons},
            dedupe_key=f"evidence:{run.id}",
        )


def maybe_create_shortlist(db, job_id: str, owner_id: str, trigger: str = "screening_completed") -> ShortlistProposal | None:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
    criteria = latest_criteria(db, job_id, approved_only=True) if job else None
    if not job or not criteria:
        return None
    active_tasks = db.scalar(
        select(func.count()).select_from(AgentTask).join(Application, AgentTask.application_id == Application.id).where(
            Application.job_id == job_id,
            AgentTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.RETRYING.value]),
        )
    ) or 0
    if active_tasks:
        return None
    config = (job.requirements or {}).get("shortlist_trigger", {})
    if config.get("enabled", True) is False:
        return None
    minimum = max(1, int(config.get("min_completed", 2)))
    top_n = min(20, max(1, int(config.get("top_n", 5))))
    min_score = float(config.get("min_score", 65))
    apps = list(db.scalars(select(Application).where(Application.job_id == job_id, Application.owner_id == owner_id)))
    eligible = [item for item in apps if "final_score" in (item.screening or {})]
    if len(eligible) < minimum:
        return None
    ranked_apps = sorted(eligible, key=lambda item: item.screening.get("final_score", 0), reverse=True)
    selected = [item for item in ranked_apps if item.screening.get("final_score", 0) >= min_score][:top_n]
    if not selected:
        selected = ranked_apps[:top_n]
    snapshot = ":".join(f"{item.id}@{item.screening.get('final_score', 0)}" for item in ranked_apps)
    digest = hashlib.sha256(snapshot.encode()).hexdigest()[:20]
    key = f"shortlist:{job_id}:{criteria.id}:{digest}"
    existing = db.scalar(select(ShortlistProposal).where(ShortlistProposal.idempotency_key == key))
    if existing:
        return existing
    pending = list(db.scalars(select(ShortlistProposal).where(
        ShortlistProposal.job_id == job_id, ShortlistProposal.status == "PENDING"
    )))
    for old in pending:
        old.status = "SUPERSEDED"
        request = db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.resource_id == old.id, ApprovalRequest.status == "PENDING"
        ))
        if request:
            request.status = "SUPERSEDED"
            request.decided_at = utcnow()
    proposal = ShortlistProposal(
        owner_id=owner_id,
        job_id=job_id,
        criteria_version_id=criteria.id,
        trigger=trigger,
        application_ids=[item.id for item in selected],
        ranking=[{"application_id": item.id, "score": item.screening.get("final_score"),
                  "confidence": item.screening.get("confidence", 0)} for item in ranked_apps],
        idempotency_key=key,
    )
    db.add(proposal)
    db.flush()
    add_approval(
        db,
        owner_id=owner_id,
        request_type="SHORTLIST",
        job_id=job_id,
        resource_id=proposal.id,
        title=f"Duyệt shortlist: {job.title}",
        summary=f"Agent đề xuất {len(selected)}/{len(eligible)} ứng viên; chưa có outreach nào được phép.",
        payload={"application_ids": proposal.application_ids, "ranking": proposal.ranking,
                 "criteria_version": criteria.version, "trigger": trigger},
        dedupe_key=f"shortlist-approval:{proposal.id}",
    )
    return proposal
