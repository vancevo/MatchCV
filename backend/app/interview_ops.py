from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .models import (
    Application,
    ApprovalRequest,
    FeedbackSummary,
    IntegrationConnection,
    Interview,
    InterviewPolicy,
    InterviewScorecard,
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
