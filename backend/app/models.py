from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.types import UserDefinedType
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .statuses import (
    ApplicationStatus,
    BatchItemStatus,
    BatchStatus,
    InterviewStatus,
    JobStatus,
    RunStatus,
    TaskStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Vector96(UserDefinedType):
    """pgvector in Postgres; migrations use JSON for the SQLite test profile."""

    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "VECTOR(96)"

    def bind_processor(self, _dialect):
        import json
        return lambda value: json.dumps(value) if value is not None else None

    def result_processor(self, _dialect, _coltype):
        import json

        def load(value):
            if value is None or isinstance(value, list):
                return value
            if isinstance(value, str):
                return json.loads(value)
            return list(value)
        return load


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(120), default="Engineering")
    location: Mapped[str] = mapped_column(String(200), default="Remote")
    description: Mapped[str] = mapped_column(Text)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UploadBatch(Base):
    __tablename__ = "upload_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=BatchStatus.PROCESSING.value)
    total: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchItem(Base):
    __tablename__ = "batch_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default=BatchItemStatus.QUEUED.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("upload_batches.id", ondelete="SET NULL"), nullable=True)
    candidate_name: Mapped[str] = mapped_column(String(200))
    candidate_email: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(32), default=ApplicationStatus.WAITING_REVIEW.value)
    resume_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resume_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    resume_text: Mapped[str] = mapped_column(Text)
    screening: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline: Mapped[list] = mapped_column(JSON, default=list)
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        UniqueConstraint("owner_id", "start_at", name="uq_interviews_owner_start"),
        UniqueConstraint("idempotency_key", name="uq_interviews_idempotency_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default=InterviewStatus.SCHEDULED.value)
    meeting_url: Mapped[str] = mapped_column(String(500))
    provider: Mapped[str] = mapped_column(String(32), default="local")
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    timezone_name: Mapped[str] = mapped_column(String(80), default="UTC")
    idempotency_key: Mapped[str] = mapped_column(String(255), default=lambda: f"interview:{uuid4()}")
    reschedule_count: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("owner_id", "provider", name="uq_integration_owner_provider"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    account_email: Mapped[str] = mapped_column(String(320), default="")
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchedulingInvitation(Base):
    __tablename__ = "scheduling_invitations"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_scheduling_invitation_token_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="SCHEDULE", index=True)
    timezone_name: Mapped[str] = mapped_column(String(80), default="Asia/Ho_Chi_Minh")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailTemplate(Base):
    __tablename__ = "email_templates"
    __table_args__ = (UniqueConstraint("owner_id", "key", "version", name="uq_email_template_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderWebhookEvent(Base):
    __tablename__ = "provider_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_provider_webhook_external"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewPolicy(Base):
    __tablename__ = "interview_policies"
    __table_args__ = (UniqueConstraint("owner_id", name="uq_interview_policies_owner"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    reminder_minutes: Mapped[list] = mapped_column(JSON, default=lambda: [1440, 60])
    max_reschedules: Mapped[int] = mapped_column(Integer, default=2)
    feedback_due_hours: Mapped[int] = mapped_column(Integer, default=24)
    timezone_name: Mapped[str] = mapped_column(String(80), default="Asia/Ho_Chi_Minh")
    working_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])
    working_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    working_end_hour: Mapped[int] = mapped_column(Integer, default=17)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BusyBlock(Base):
    """A one-off period nobody is available, such as leave or an external meeting."""

    __tablename__ = "busy_blocks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InterviewScorecard(Base):
    __tablename__ = "interview_scorecards"
    __table_args__ = (UniqueConstraint("interview_id", "interviewer_email", name="uq_scorecard_interviewer"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    interviewer_email: Mapped[str] = mapped_column(String(320))
    rubric: Mapped[list] = mapped_column(JSON, default=list)
    answers: Mapped[list] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedbackSummary(Base):
    __tablename__ = "feedback_summaries"
    __table_args__ = (UniqueConstraint("interview_id", "version", name="uq_feedback_summary_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    criteria_version_id: Mapped[str | None] = mapped_column(ForeignKey("criteria_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True)
    workflow: Mapped[str] = mapped_column(String(80), default="candidate_screening")
    trigger: Mapped[str] = mapped_column(String(80), default="batch_upload")
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.QUEUED.value)
    current_node: Mapped[str] = mapped_column(String(80), default="queued")
    provider: Mapped[str] = mapped_column(String(80), default="rules")
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(120), default="screening-v1+interview-kit-v1")
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    trace_json: Mapped[dict] = mapped_column("trace", JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    node: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_agent_tasks_idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    batch_item_id: Mapped[str | None] = mapped_column(ForeignKey("batch_items.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    queue_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CriteriaVersion(Base):
    __tablename__ = "criteria_versions"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_criteria_versions_job_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    criteria: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("criteria_versions.id", ondelete="SET NULL"), nullable=True)
    change_note: Mapped[str] = mapped_column(Text, default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreeningArtifact(Base):
    __tablename__ = "screening_artifacts"
    __table_args__ = (UniqueConstraint("run_id", name="uq_screening_artifacts_run_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    criteria_version_id: Mapped[str] = mapped_column(ForeignKey("criteria_versions.id", ondelete="CASCADE"), index=True)
    result: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list] = mapped_column(Vector96())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShortlistProposal(Base):
    __tablename__ = "shortlist_proposals"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_shortlist_proposals_idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    criteria_version_id: Mapped[str] = mapped_column(ForeignKey("criteria_versions.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    trigger: Mapped[str] = mapped_column(String(80), default="screening_completed")
    application_ids: Mapped[list] = mapped_column(JSON, default=list)
    ranking: Mapped[list] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_approval_requests_dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    request_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=True, index=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    requested_by_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(32), default="RECRUITER")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TenantPolicy(Base):
    __tablename__ = "tenant_policies"
    __table_args__ = (UniqueConstraint("owner_id", name="uq_tenant_policy_owner"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    monthly_screening_limit: Mapped[int] = mapped_column(Integer, default=10000)
    screenings_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    monthly_token_limit: Mapped[int] = mapped_column(Integer, default=10000000)
    monthly_cost_limit_micros: Mapped[int] = mapped_column(Integer, default=100000000)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    calendar_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mail_sandbox_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mail_sandbox_base_email: Mapped[str] = mapped_column(String(320), default="")
    mail_sandbox_max_alias: Mapped[int] = mapped_column(Integer, default=100)
    # Addresses cleared for real delivery beyond the base mailbox and its plus aliases.
    mail_sandbox_allowed_emails: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TenantUsage(Base):
    __tablename__ = "tenant_usage"
    __table_args__ = (UniqueConstraint("owner_id", "period", name="uq_tenant_usage_period"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    screenings: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TenantRateWindow(Base):
    __tablename__ = "tenant_rate_windows"
    __table_args__ = (UniqueConstraint("owner_id", "window_key", name="uq_tenant_rate_window"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    window_key: Mapped[str] = mapped_column(String(16), index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SourceConnector(Base):
    __tablename__ = "source_connectors"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_source_connector_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["resumes.write"])
    secret_hash: Mapped[str] = mapped_column(String(64))
    consent_basis: Mapped[str] = mapped_column(String(240))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceIngestion(Base):
    __tablename__ = "source_ingestions"
    __table_args__ = (UniqueConstraint("connector_id", "source_ref", name="uq_source_ingestion_ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("source_connectors.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True)
    source_ref: Mapped[str] = mapped_column(String(255))
    source_uri: Mapped[str] = mapped_column(String(1000), default="")
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    candidate_consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelPolicy(Base):
    __tablename__ = "model_policies"
    __table_args__ = (UniqueConstraint("owner_id", "workflow", name="uq_model_policy_workflow"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    workflow: Mapped[str] = mapped_column(String(80), default="candidate_screening")
    champion: Mapped[dict] = mapped_column(JSON, default=dict)
    challenger: Mapped[dict] = mapped_column(JSON, default=dict)
    challenger_percent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    evaluation_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OperationalSLOPolicy(Base):
    __tablename__ = "operational_slo_policies"
    __table_args__ = (UniqueConstraint("owner_id", name="uq_operational_slo_policy_owner"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    window_hours: Mapped[int] = mapped_column(Integer, default=24)
    min_screening_success_rate: Mapped[float] = mapped_column(Float, default=0.95)
    max_p95_latency_ms: Mapped[int] = mapped_column(Integer, default=120000)
    max_due_outbox: Mapped[int] = mapped_column(Integer, default=0)
    max_failed_outbox: Mapped[int] = mapped_column(Integer, default=0)
    max_stale_approvals: Mapped[int] = mapped_column(Integer, default=0)
    budget_warning_percent: Mapped[int] = mapped_column(Integer, default=80)
    min_canary_samples: Mapped[int] = mapped_column(Integer, default=20)
    max_success_rate_drop: Mapped[float] = mapped_column(Float, default=0.02)
    max_latency_regression_percent: Mapped[int] = mapped_column(Integer, default=20)
    max_cost_regression_percent: Mapped[int] = mapped_column(Integer, default=15)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_email: Mapped[str] = mapped_column(String(320), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"
    __table_args__ = (UniqueConstraint("owner_id", "signal", name="uq_operational_alert_signal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    signal: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="WARNING", index=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    summary: Mapped[str] = mapped_column(String(300))
    observed: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold: Mapped[dict] = mapped_column(JSON, default=dict)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReleaseGate(Base):
    __tablename__ = "release_gates"
    __table_args__ = (UniqueConstraint("owner_id", "release_version", name="uq_release_gate_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    release_version: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    baseline: Mapped[dict] = mapped_column(JSON)
    candidate: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
