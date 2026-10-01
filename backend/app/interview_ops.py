from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .config import get_settings
from .llm import cross_analyze_interview_ai, segment_transcript_ai
from .models import (
    Application,
    ApprovalRequest,
    FeedbackSummary,
    IntegrationConnection,
    Interview,
    InterviewClaim,
    InterviewCrossAnalysisRun,
    InterviewPolicy,
    InterviewQAClaimLink,
    InterviewQAPair,
    InterviewQARequirementLink,
    InterviewScorecard,
    InterviewTranscriptSession,
    Job,
    OutboxEvent,
    SchedulingInvitation,
)
from .scheduling import add_outbox, dispatch_outbox, local_datetime_label, render_template


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


def get_policy(db, owner_id: str) -> InterviewPolicy:
    policy = db.scalar(select(InterviewPolicy).where(InterviewPolicy.owner_id == owner_id))
    if not policy:
        policy = InterviewPolicy(owner_id=owner_id)
        db.add(policy)
        db.flush()
    return policy


def escalation(db, interview: Interview, reason: str, summary: str, payload: dict | None = None) -> ApprovalRequest:
    dedupe_key = f"interview-escalation:{interview.id}:{reason}"
    existing = db.scalar(select(ApprovalRequest).where(ApprovalRequest.dedupe_key == dedupe_key))
    if existing:
        return existing
    application = db.get(Application, interview.application_id)
    value = ApprovalRequest(
        owner_id=interview.owner_id,
        request_type="ESCALATION",
        status="PENDING",
        job_id=application.job_id if application else None,
        application_id=interview.application_id,
        resource_id=interview.id,
        title=f"Ngoại lệ phỏng vấn: {reason.replace('_', ' ').lower()}",
        summary=summary,
        payload={"reason": reason, "interview_id": interview.id, **(payload or {})},
        dedupe_key=dedupe_key,
    )
    db.add(value)
    db.flush()
    return value


def schedule_reminders(db, interview: Interview) -> list[str]:
    """Replace pending reminders after create/reschedule and return immediately-due ids."""
    for old in db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == interview.id,
        OutboxEvent.operation == "EMAIL_SEND",
        OutboxEvent.status == "PENDING",
        OutboxEvent.idempotency_key.like(f"interview-reminder:{interview.id}:%"),
    )):
        old.status = "CANCELLED"

    policy = get_policy(db, interview.owner_id)
    application = db.get(Application, interview.application_id)
    job = db.get(Job, application.job_id) if application else None
    if not application or not job or not application.candidate_email:
        return []
    dispatch_now: list[str] = []
    for minutes in sorted({int(value) for value in policy.reminder_minutes if int(value) > 0}, reverse=True):
        due_at = aware(interview.start_at) - timedelta(minutes=minutes)
        if due_at <= utcnow() - timedelta(minutes=5):
            continue
        rendered = render_template(
            db,
            interview.owner_id,
            "interview_reminder",
            {
                "candidate_name": application.candidate_name,
                "job_title": job.title,
                "start_at": local_datetime_label(aware(interview.start_at), interview.timezone_name),
                "meeting_url": interview.meeting_url or "(sẽ gửi trước buổi phỏng vấn)",
            },
        )
        event = add_outbox(
            db,
            owner_id=interview.owner_id,
            aggregate_type="interview",
            aggregate_id=interview.id,
            operation="EMAIL_SEND",
            payload={"to": application.candidate_email, **rendered, "kind": "INTERVIEW_REMINDER"},
            idempotency_key=f"interview-reminder:{interview.id}:{interview.reschedule_count}:{minutes}",
        )
        event.available_at = due_at
        dispatch_now.append(event.id)
    return dispatch_now


def schedule_scorecard_reminder(db, interview: Interview) -> str:
    for old in db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == interview.id, OutboxEvent.operation == "EMAIL_SEND",
        OutboxEvent.status == "PENDING",
        OutboxEvent.idempotency_key.like(f"scorecard-reminder:{interview.id}:%"),
    )):
        old.status = "CANCELLED"
    policy = get_policy(db, interview.owner_id)
    connection = db.scalar(select(IntegrationConnection).where(
        IntegrationConnection.owner_id == interview.owner_id,
        IntegrationConnection.status == "ACTIVE",
    ))
    application = db.get(Application, interview.application_id)
    job = db.get(Job, application.job_id) if application else None
    due_at = aware(interview.end_at) + timedelta(hours=max(1, policy.feedback_due_hours // 2))
    rendered = render_template(
        db, interview.owner_id, "scorecard_reminder",
        {"candidate_name": application.candidate_name if application else "ứng viên",
         "job_title": job.title if job else "vị trí tuyển dụng",
         "deadline": local_datetime_label(
             aware(interview.end_at) + timedelta(hours=policy.feedback_due_hours),
             interview.timezone_name)},
    )
    event = add_outbox(
        db, owner_id=interview.owner_id, aggregate_type="interview", aggregate_id=interview.id,
        operation="EMAIL_SEND", payload={"to": connection.account_email if connection else "", **rendered,
                                         "kind": "SCORECARD_REMINDER"},
        idempotency_key=f"scorecard-reminder:{interview.id}:{interview.reschedule_count}",
    )
    event.available_at = due_at
    return event.id


def summarize_feedback(db, interview: Interview) -> FeedbackSummary | None:
    scorecards = list(db.scalars(select(InterviewScorecard).where(
        InterviewScorecard.interview_id == interview.id,
        InterviewScorecard.owner_id == interview.owner_id,
    ).order_by(InterviewScorecard.submitted_at)))
    if not scorecards:
        return None
    ratings: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for card in scorecards:
        for answer in card.answers or []:
            criterion = str(answer.get("criterion", "")).strip()
            if criterion:
                ratings[criterion].append((int(answer.get("rating", 0)), card.id, card.interviewer_email))
    strengths = [name for name, values in ratings.items() if sum(v[0] for v in values) / len(values) >= 4]
    concerns = [name for name, values in ratings.items() if sum(v[0] for v in values) / len(values) <= 2.5]
    conflicts = [
        {"criterion": name, "ratings": [v[0] for v in values], "scorecard_ids": [v[1] for v in values]}
        for name, values in ratings.items() if len(values) > 1 and max(v[0] for v in values) - min(v[0] for v in values) >= 2
    ]
    recommendations = [card.recommendation for card in scorecards]
    if len(set(recommendations)) > 1:
        conflicts.append({"criterion": "recommendation", "values": recommendations,
                          "scorecard_ids": [card.id for card in scorecards]})
    summary_text = (
        f"Đã tổng hợp {len(scorecards)} scorecard. "
        f"{len(strengths)} điểm mạnh, {len(concerns)} điểm cần lưu ý và {len(conflicts)} mâu thuẫn. "
        "Tóm tắt này hỗ trợ review, không phải quyết định tuyển dụng."
    )
    version = (db.scalar(select(func.max(FeedbackSummary.version)).where(
        FeedbackSummary.interview_id == interview.id
    )) or 0) + 1
    value = FeedbackSummary(
        owner_id=interview.owner_id,
        interview_id=interview.id,
        version=version,
        summary=summary_text,
        strengths=strengths,
        concerns=concerns,
        conflicts=conflicts,
        sources=[{"scorecard_id": card.id, "interviewer_email": card.interviewer_email} for card in scorecards],
    )
    db.add(value)
    db.flush()
    if conflicts:
        escalation(db, interview, "CONFLICTING_FEEDBACK", "Các scorecard có đánh giá mâu thuẫn cần hiring team xem lại.",
                   {"feedback_summary_id": value.id, "conflict_count": len(conflicts)})
    return value


def _claims_payload_from_screening(screening: dict) -> list[dict]:
    """Stage 1 - reuse what screen_candidate_ai already extracted/verified, no new LLM call. Plain
    dicts (no DB row, no id yet) so the AI pipeline below can run without any open DB session."""
    profile = screening.get("candidate_profile") or {}
    claims: list[dict] = []
    for skill in profile.get("skills", []) or []:
        text = str(skill).strip()
        if text:
            claims.append({"text": f"Kỹ năng: {text}", "source_quote": "", "category": "skill"})
    years = profile.get("experience_years")
    if years:
        claims.append({"text": f"{years} năm kinh nghiệm", "source_quote": "", "category": "experience"})
    for item in screening.get("evidence", []) or []:
        if isinstance(item, dict) and item.get("matched") and item.get("evidence"):
            claims.append({"text": str(item.get("requirement", "")).strip(),
                          "source_quote": str(item.get("evidence", "")).strip(), "category": "requirement"})
    for index, claim in enumerate(claims):
        claim["local_id"] = f"c{index}"
    return claims


async def run_cross_analysis_pipeline(
    transcript_text: str, candidate_name: str, candidate_email: str,
    job_title: str, requirements: dict, screening: dict,
) -> dict:
    """Stage 0 (segment, one model) -> Stage 1 (claims, no LLM) -> Stage 2 (cross-analysis, fanned
    out across every model in settings.interview_analysis_models, concurrently). Pure computation -
    takes no `db` and performs no DB access, because this can run for tens of seconds (4 concurrent
    network calls) and SQLite cannot tolerate a write transaction held open that long alongside any
    other request (this used to raise "database is locked" until this was split out). Returns a
    plain dict for persist_cross_analysis() to write in one short transaction afterwards."""
    qa_pairs_raw = await segment_transcript_ai(transcript_text, candidate_name, candidate_email)
    claims_payload = _claims_payload_from_screening(screening)
    llm_claims = [{"id": claim["local_id"], "text": claim["text"]} for claim in claims_payload]
    llm_qa_pairs = [{"speaker_role": pair["speaker_role"], "question": pair["question"], "answer": pair["answer"]}
                    for pair in qa_pairs_raw]

    async def _run_one(model_name: str) -> dict:
        started = time.monotonic()
        try:
            result = await cross_analyze_interview_ai(model_name, job_title, requirements, llm_claims, llm_qa_pairs)
        except Exception as exc:  # defensive - _complete() already swallows transport/parse errors
            return {"model_name": model_name, "status": "ERROR", "error": str(exc)[:500],
                    "latency_ms": int((time.monotonic() - started) * 1000), "qa_analyses": [], "overall": None}
        latency_ms = int((time.monotonic() - started) * 1000)
        if result is None:
            unavailable_reason = (
                "Colab/Ollama chưa cấu hình hoặc tunnel không phản hồi" if model_name.startswith("colab:")
                else "OpenRouter chưa cấu hình hoặc model đang bị giới hạn"
            )
            return {"model_name": model_name, "status": "UNAVAILABLE", "error": unavailable_reason,
                    "latency_ms": latency_ms, "qa_analyses": [], "overall": None}
        return {"model_name": model_name, "status": "OK", "error": None, "latency_ms": latency_ms,
                "qa_analyses": result["qa_analyses"], "overall": result["overall"]}

    configured_models = get_settings().interview_analysis_models
    # "colab:" models all share one physical GPU/tunnel - running them concurrently forces Ollama to
    # swap each model in and out of VRAM mid-flight, which easily pushes past a free Cloudflare quick
    # tunnel's ~100s edge timeout (confirmed: a single model alone answers fine in under 90s). Running
    # them one at a time instead gives each the GPU's full, uncontended attention. OpenRouter models
    # hit independent providers, so they keep running concurrently as before.
    colab_models = [name for name in configured_models if name.startswith("colab:")]
    other_models = [name for name in configured_models if not name.startswith("colab:")]
    model_runs = list(await asyncio.gather(*[_run_one(name) for name in other_models]))
    for name in colab_models:
        model_runs.append(await _run_one(name))
    return {"qa_pairs": qa_pairs_raw, "claims": claims_payload, "model_runs": model_runs}


def persist_cross_analysis(db, session: InterviewTranscriptSession, pipeline_result: dict) -> None:
    """Short, DB-only write of everything run_cross_analysis_pipeline() computed - kept separate so
    the slow network calls above never hold this transaction open."""
    qa_rows = [
        InterviewQAPair(session_id=session.id, order_index=index, speaker_role=pair["speaker_role"],
                        question_text=pair["question"], answer_text=pair["answer"])
        for index, pair in enumerate(pipeline_result["qa_pairs"])
    ]
    db.add_all(qa_rows)

    claim_rows = [
        InterviewClaim(session_id=session.id, text=claim["text"], source_quote=claim["source_quote"],
                       category=claim["category"])
        for claim in pipeline_result["claims"]
    ]
    db.add_all(claim_rows)
    db.flush()  # need generated ids before resolving the local_id references in the links below
    claim_id_by_local = {claim["local_id"]: row.id for claim, row in zip(pipeline_result["claims"], claim_rows)}

    for run in pipeline_result["model_runs"]:
        overall = run.get("overall") or {}
        db.add(InterviewCrossAnalysisRun(
            session_id=session.id, model_name=run["model_name"], status=run["status"],
            error=run["error"], latency_ms=run["latency_ms"],
            score=overall.get("score"), recommendation=overall.get("recommendation"), summary=overall.get("reasoning"),
        ))
        for item in run["qa_analyses"]:
            qa_row = qa_rows[item["qa_index"]]
            for claim in item["linked_claims"]:
                real_claim_id = claim_id_by_local.get(claim["claim_id"])
                if not real_claim_id:
                    continue
                db.add(InterviewQAClaimLink(
                    qa_id=qa_row.id, claim_id=real_claim_id, model_name=run["model_name"],
                    relationship_type=claim["relationship"], evidence_quote=claim["evidence_quote"],
                    confidence=claim["confidence"],
                ))
            for req in item["linked_requirements"]:
                db.add(InterviewQARequirementLink(
                    qa_id=qa_row.id, requirement_text=req["requirement"], model_name=run["model_name"],
                    evidence_strength=req["evidence_strength"], evidence_quote=req["evidence_quote"],
                ))
    db.flush()


def aggregate_cross_analysis(db, session_id: str, model_name: str, known_requirements: set[str]) -> dict:
    """Stage 3 - deterministic roll-up over the stored link rows, same philosophy as
    summarize_feedback(): always consistent with the evidence, never a separately drifting copy."""
    claims = list(db.scalars(select(InterviewClaim).where(InterviewClaim.session_id == session_id)))
    claim_links = list(db.scalars(
        select(InterviewQAClaimLink).join(InterviewQAPair, InterviewQAClaimLink.qa_id == InterviewQAPair.id)
        .where(InterviewQAPair.session_id == session_id, InterviewQAClaimLink.model_name == model_name)
    ))
    requirement_links = list(db.scalars(
        select(InterviewQARequirementLink).join(InterviewQAPair, InterviewQARequirementLink.qa_id == InterviewQAPair.id)
        .where(InterviewQAPair.session_id == session_id, InterviewQARequirementLink.model_name == model_name)
    ))
    by_claim_id = {claim.id: claim.text for claim in claims}
    linked_claim_ids = {link.claim_id for link in claim_links}
    verified = {link.claim_id for link in claim_links if link.relationship_type == "VERIFIED"}
    inconsistent = {link.claim_id for link in claim_links if link.relationship_type == "POTENTIAL_INCONSISTENCY"}
    covered = {link.requirement_text for link in requirement_links if link.evidence_strength in ("STRONG", "WEAK")}
    return {
        "claims_verified": [by_claim_id[i] for i in verified if i in by_claim_id],
        "claims_unverified": [claim.text for claim in claims if claim.id not in linked_claim_ids],
        "potential_inconsistencies": [by_claim_id[i] for i in inconsistent if i in by_claim_id],
        "new_info_beyond_cv": [link.evidence_quote for link in claim_links
                               if link.relationship_type == "NEW_INFO" and link.evidence_quote],
        "requirements_covered": sorted(covered),
        "requirements_not_covered": sorted(known_requirements - covered),
    }


def rule_based_assessment(aggregation: dict) -> dict:
    """Stage 3b - a fully deterministic, reproducible companion score computed only from the
    verified-evidence counts in `aggregation` above (never from free-form LLM judgment), so it can
    be recomputed by hand and compared against that same model's own holistic score/recommendation
    (Stage 2's `overall_assessment`) as an agreement check. The LLM's score is a subjective "does
    this candidate seem strong" judgment; this one is "how much of what was claimed/asked actually
    got verified against the transcript" - two different, complementary signals.

    Weighting: JD requirement coverage counts for more (0.6) than CV claim verification (0.4),
    since demonstrating the job's actual required skills live in conversation is more directly
    job-relevant than merely corroborating what the CV already claimed. Each confirmed
    inconsistency (a CV claim the conversation contradicted) is a flat -15 point penalty (capped
    at -40 total) - one clear contradiction is a discrete red flag that shouldn't get diluted by
    averaging it over a large claim count.
    """
    claims_total = len(aggregation["claims_verified"]) + len(aggregation["claims_unverified"])
    requirements_total = len(aggregation["requirements_covered"]) + len(aggregation["requirements_not_covered"])
    claims_ratio = len(aggregation["claims_verified"]) / claims_total if claims_total else None
    requirements_ratio = (
        len(aggregation["requirements_covered"]) / requirements_total if requirements_total else None
    )

    if requirements_ratio is None and claims_ratio is None:
        base = 0.0
    elif requirements_ratio is None:
        base = claims_ratio
    elif claims_ratio is None:
        base = requirements_ratio
    else:
        base = 0.6 * requirements_ratio + 0.4 * claims_ratio

    penalty = min(40, len(aggregation["potential_inconsistencies"]) * 15)
    score = max(0, min(100, round(base * 100 - penalty)))

    if score >= 85:
        recommendation = "STRONG_YES"
    elif score >= 65:
        recommendation = "YES"
    elif score >= 40:
        recommendation = "MIXED"
    elif score >= 20:
        recommendation = "NO"
    else:
        recommendation = "STRONG_NO"

    return {
        "score": score,
        "recommendation": recommendation,
        "requirements_ratio": round(requirements_ratio, 2) if requirements_ratio is not None else None,
        "claims_ratio": round(claims_ratio, 2) if claims_ratio is not None else None,
        "inconsistency_penalty": penalty,
    }


def run_follow_up_cycle(owner_id: str | None = None) -> dict:
    """Idempotent sweep suitable for a cron/RQ scheduled job and manual operations run."""
    due_ids: list[str] = []
    expired = feedback_overdue = 0
    now = utcnow()
    from .database import session_scope
    with session_scope() as db:
        due_statement = select(OutboxEvent.id).where(OutboxEvent.status == "PENDING", OutboxEvent.available_at <= now)
        invitation_statement = select(SchedulingInvitation).where(
            SchedulingInvitation.status == "ACTIVE", SchedulingInvitation.purpose == "SCHEDULE",
            SchedulingInvitation.expires_at <= now)
        interview_statement = select(Interview).where(
            Interview.status.in_(["SCHEDULED", "FEEDBACK_PENDING"]), Interview.end_at < now)
        if owner_id:
            due_statement = due_statement.where(OutboxEvent.owner_id == owner_id)
            invitation_statement = invitation_statement.where(SchedulingInvitation.owner_id == owner_id)
            interview_statement = interview_statement.where(Interview.owner_id == owner_id)
        due_ids = list(db.scalars(due_statement.limit(200)))
        for invitation in db.scalars(invitation_statement):
            invitation.status = "EXPIRED"
            interview = db.scalar(select(Interview).where(
                Interview.application_id == invitation.application_id,
                Interview.owner_id == invitation.owner_id,
            ).order_by(Interview.updated_at.desc()))
            if interview:
                escalation(db, interview, "SCHEDULING_TIMEOUT", "Ứng viên chưa chọn lịch trước khi liên kết hết hạn.")
            expired += 1
        for interview in db.scalars(interview_statement):
            interview.status = "FEEDBACK_PENDING"
            policy = get_policy(db, interview.owner_id)
            submitted = db.scalar(select(func.count()).select_from(InterviewScorecard).where(
                InterviewScorecard.interview_id == interview.id,
            )) or 0
            if not submitted and aware(interview.end_at) + timedelta(hours=policy.feedback_due_hours) <= now:
                escalation(db, interview, "FEEDBACK_TIMEOUT", "Scorecard chưa được nộp đúng hạn.")
                feedback_overdue += 1
    for event_id in due_ids:
        dispatch_outbox(event_id)
    return {"outbox_dispatched": len(due_ids), "invitations_expired": expired,
            "feedback_overdue": feedback_overdue}


def schedule_follow_up_sweep(owner_id: str, at: datetime, key: str) -> None:
    """Wake the idempotent sweep at a policy deadline when using the durable queue."""
    from .config import get_settings
    settings = get_settings()
    if settings.queue_eager:
        return
    from redis import Redis
    from rq import Queue
    Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url)).enqueue_at(
        aware(at), run_follow_up_cycle, owner_id, job_id=f"follow-up:{key}", retry=None,
    )
