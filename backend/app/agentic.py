"""Bounded Phase-2 decisions: versioned criteria, calibrated screening and proposals."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from .governance import policy_for
from .models import (
    AgentRun,
    AgentTask,
    Application,
    ApprovalRequest,
    AuditLog,
    CriteriaVersion,
    Job,
    ScreeningArtifact,
    ShortlistProposal,
)
from .statuses import ApplicationStatus, TaskStatus


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


def calibrate_screening(result: dict, resume_text: str, criteria: dict, low_confidence_threshold: float = 65.0) -> tuple[dict, list[float]]:
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
    if confidence < low_confidence_threshold / 100:
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


def route_scored_application(db, application: Application, owner_id: str) -> str:
    """Apply the tenant's score-threshold policy right after screening finishes.

    Below the reject threshold the candidate is dropped immediately with no human
    step. Everything else goes to WAITING_REVIEW; `maybe_create_shortlist` then
    decides whether it lands in the shortlist queue as an "approve" or "review" row.
    """
    policy = policy_for(db, owner_id)
    score = float((application.screening or {}).get("final_score", 0))
    if score < policy.auto_reject_threshold:
        application.status = ApplicationStatus.REJECTED.value
        db.add(AuditLog(
            owner_id=owner_id,
            application_id=application.id,
            action="APPLICATION_AUTO_REJECTED",
            metadata_json={"score": score, "threshold": policy.auto_reject_threshold},
        ))
        return application.status
    application.status = ApplicationStatus.WAITING_REVIEW.value
    return application.status


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


def create_criteria_version(db, job: Job, owner_id: str, criteria: dict, note: str = "",
                            actor: dict[str, str] | None = None) -> CriteriaVersion:
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
        actor=actor,
    )
    return version


def add_approval(db, *, owner_id: str, request_type: str, resource_id: str, title: str,
                 summary: str, payload: dict, dedupe_key: str, job_id: str | None = None,
                 application_id: str | None = None, actor: dict[str, str] | None = None) -> ApprovalRequest:
    """Pass `actor` when a person raised this; leave it out so agent-raised requests stay unattributed."""
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
        requested_by_id=(actor or {}).get("id") or None,
        requested_by_email=(actor or {}).get("email") or None,
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


def _band(item: Application, approve_at: float, min_confidence: float) -> str:
    """"Duyệt ngay" needs both a strong match AND the model being sure of its own evidence read."""
    score = item.screening.get("final_score", 0)
    confidence = item.screening.get("confidence", 0) * 100
    return "APPROVE" if score >= approve_at and confidence >= min_confidence else "REVIEW"


def is_ready_for_approval(item: Application, policy) -> bool:
    """The "Chờ duyệt" bucket: WAITING_REVIEW and clears both bars, so it never needed the
    shortlist/Phê duyệt detour — everything else in WAITING_REVIEW is still being triaged there."""
    if item.status != ApplicationStatus.WAITING_REVIEW.value or "final_score" not in (item.screening or {}):
        return False
    return _band(item, policy.auto_approve_threshold, policy.min_confidence_threshold) == "APPROVE"


def maybe_create_shortlist(db, job_id: str, owner_id: str, trigger: str = "screening_completed") -> ShortlistProposal | None:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
    criteria = latest_criteria(db, job_id, approved_only=True) if job else None
    if not job or not criteria:
        return None
    config = (job.requirements or {}).get("shortlist_trigger", {})
    if config.get("enabled", True) is False:
        return None
    apps = list(db.scalars(select(Application).where(Application.job_id == job_id, Application.owner_id == owner_id)))

    if trigger == "screening_completed":
        # A CV that clears BOTH the score and confidence bar is a clear yes — it sits in
        # WAITING_REVIEW same as everyone else and the recruiter can act on it straight from the
        # candidate list, no committee needed. Only the "still needs a closer look" CVs (clears the
        # reject bar but not both approve bars) go into this shortlist/Phê duyệt queue at all.
        policy = policy_for(db, owner_id)
        approve_at = policy.auto_approve_threshold
        min_confidence = policy.min_confidence_threshold
        eligible = [item for item in apps if item.status == ApplicationStatus.WAITING_REVIEW.value
                    and "final_score" in (item.screening or {})
                    and _band(item, approve_at, min_confidence) == "REVIEW"]
        if not eligible:
            stale = db.scalar(select(ShortlistProposal).where(
                ShortlistProposal.job_id == job_id, ShortlistProposal.status == "PENDING"
            ))
            if stale:
                stale.status = "SUPERSEDED"
                request = db.scalar(select(ApprovalRequest).where(
                    ApprovalRequest.resource_id == stale.id, ApprovalRequest.status == "PENDING"
                ))
                if request:
                    request.status = "SUPERSEDED"
                    request.decided_at = utcnow()
            return None
        selected = sorted(eligible, key=lambda item: item.screening.get("final_score", 0), reverse=True)
    else:
        active_tasks = db.scalar(
            select(func.count()).select_from(AgentTask).join(Application, AgentTask.application_id == Application.id).where(
                Application.job_id == job_id,
                AgentTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.RETRYING.value]),
            )
        ) or 0
        if active_tasks:
            return None
        minimum = max(1, int(config.get("min_completed", 2)))
        top_n = min(20, max(1, int(config.get("top_n", 5))))
        min_score = float(config.get("min_score", 65))
        approve_at = min_score
        min_confidence = 0.0
        eligible = [item for item in apps if "final_score" in (item.screening or {})]
        if len(eligible) < minimum:
            return None
        ranked_apps = sorted(eligible, key=lambda item: item.screening.get("final_score", 0), reverse=True)
        selected = [item for item in ranked_apps if item.screening.get("final_score", 0) >= min_score][:top_n]
        if not selected:
            selected = ranked_apps[:top_n]

    snapshot = ":".join(f"{item.id}@{item.screening.get('final_score', 0)}" for item in selected)
    digest = hashlib.sha256(snapshot.encode()).hexdigest()[:20]
    key = f"shortlist:{job_id}:{criteria.id}:{digest}"
    existing = db.scalar(select(ShortlistProposal).where(ShortlistProposal.idempotency_key == key))
    if existing:
        # The digest only tracks who is eligible (id+score) — it says nothing about who was
        # individually pulled out (approve_shortlist_item shrinks application_ids in place),
        # whether the approve/confidence threshold moved since this row was written, or whether
        # this exact roster was already resolved and later reinstated by a threshold resync.
        # Re-sync (and, if needed, revive) rather than hand back a proposal that looks right by
        # hash but is stale or dead.
        new_application_ids = [item.id for item in selected]
        new_ranking = [{"application_id": item.id, "score": item.screening.get("final_score"),
                        "confidence": item.screening.get("confidence", 0),
                        "band": _band(item, approve_at, min_confidence)} for item in selected]
        request = db.scalar(select(ApprovalRequest).where(ApprovalRequest.resource_id == existing.id))
        if existing.status != "PENDING":
            other_pending = list(db.scalars(select(ShortlistProposal).where(
                ShortlistProposal.job_id == job_id, ShortlistProposal.status == "PENDING",
                ShortlistProposal.id != existing.id,
            )))
            for old in other_pending:
                old.status = "SUPERSEDED"
                old_request = db.scalar(select(ApprovalRequest).where(
                    ApprovalRequest.resource_id == old.id, ApprovalRequest.status == "PENDING"
                ))
                if old_request:
                    old_request.status = "SUPERSEDED"
                    old_request.decided_at = utcnow()
            existing.status = "PENDING"
            existing.decision_note = ""
            existing.decided_at = None
            if request:
                request.status = "PENDING"
                request.resolution = {}
                request.decided_at = None
        if existing.application_ids != new_application_ids or existing.ranking != new_ranking:
            existing.application_ids = new_application_ids
            existing.ranking = new_ranking
            if request:
                request.payload = {**request.payload, "application_ids": existing.application_ids,
                                    "ranking": existing.ranking}
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
                  "confidence": item.screening.get("confidence", 0),
                  "band": _band(item, approve_at, min_confidence)}
                 for item in selected],
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


def resync_score_routing(db, owner_id: str) -> dict:
    """Re-apply the tenant's current score thresholds to every application that no human has
    decided on yet, so moving a slider takes effect immediately instead of only on the next
    screening. An application is left alone the moment a recruiter has acted on it (`review` set,
    or it has moved past WAITING_REVIEW/auto-REJECTED into shortlisted/interviewing/archived/etc.).
    """
    policy = policy_for(db, owner_id)
    apps = list(db.scalars(select(Application).where(
        Application.owner_id == owner_id,
        Application.status.in_([ApplicationStatus.WAITING_REVIEW.value, ApplicationStatus.REJECTED.value]),
    )))
    moved_to_rejected = 0
    moved_to_waiting = 0
    for application in apps:
        if application.review:
            continue
        score = (application.screening or {}).get("final_score")
        if score is None:
            continue
        if application.status == ApplicationStatus.WAITING_REVIEW.value and score < policy.auto_reject_threshold:
            application.status = ApplicationStatus.REJECTED.value
            db.add(AuditLog(owner_id=owner_id, application_id=application.id, action="APPLICATION_AUTO_REJECTED",
                            metadata_json={"score": score, "threshold": policy.auto_reject_threshold, "reason": "threshold_updated"}))
            moved_to_rejected += 1
        elif application.status == ApplicationStatus.REJECTED.value and score >= policy.auto_reject_threshold:
            application.status = ApplicationStatus.WAITING_REVIEW.value
            db.add(AuditLog(owner_id=owner_id, application_id=application.id, action="APPLICATION_AUTO_REINSTATED",
                            metadata_json={"score": score, "threshold": policy.auto_reject_threshold, "reason": "threshold_updated"}))
            moved_to_waiting += 1
    db.flush()
    waiting_jobs = {a.job_id for a in apps if a.status == ApplicationStatus.WAITING_REVIEW.value}
    pending_proposal_jobs = {p.job_id for p in db.scalars(select(ShortlistProposal).where(
        ShortlistProposal.owner_id == owner_id, ShortlistProposal.status == "PENDING"
    ))}
    for job_id in waiting_jobs | pending_proposal_jobs:
        maybe_create_shortlist(db, job_id, owner_id)
    return {"moved_to_rejected": moved_to_rejected, "moved_to_waiting_review": moved_to_waiting}
