from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete as sql_delete, func, or_, select, update as sql_update
from sqlalchemy.exc import IntegrityError

from .agentic import (
    calibrate_screening,
    create_criteria_version,
    ensure_criteria_version,
    latest_criteria,
    maybe_create_shortlist,
    record_screening_artifact,
    utcnow,
)
from .auth import current_tenant_id, current_user_id, require_tenant_role
from .config import get_settings
from .database import SessionLocal, session_scope
from .llm import extract_requirements_ai, generate_interview_kit_ai, llm_config, screen_candidate_ai
from .master_seed import create_master_user, has_master_seed_config
from .models import (
    AgentRun, AgentStep, AgentTask, Application, ApprovalRequest, AuditLog, BatchItem,
    CriteriaVersion, EmailTemplate, FeedbackSummary, IntegrationConnection, Interview,
    InterviewPolicy, InterviewScorecard, Job, OutboxEvent, ProviderWebhookEvent,
    SchedulingInvitation, ScreeningArtifact, ShortlistProposal, SourceConnector, SourceIngestion, Tenant,
    TenantMembership, TenantPolicy, TenantUsage, ModelPolicy, OperationalAlert, OperationalSLOPolicy,
    ReleaseGate, UploadBatch,
)
from .pipeline import extract_requirements, generate_interview_kit, screen_candidate
from .resume import candidate_identity, checksum, extract_resume
from .statuses import (
    ApplicationStatus,
    BatchItemStatus,
    BatchStatus,
    InterviewStatus,
    JobStatus,
    RunStatus,
    ReviewDecision,
    TaskStatus,
)
from .task_queue import enqueue_screening_async, queue_summary
from .worker import mark_enqueue_failed
from .workflow import screening_pipeline
from .scheduling import (
    add_outbox, authorization_url, available_slots_for_owner, decode_oauth_state,
    dispatch_outbox, exchange_code, mail_sandbox_alias, render_template, save_connection,
)
from .interview_ops import escalation, get_policy, run_follow_up_cycle, schedule_follow_up_sweep, summarize_feedback
from .governance import (
    EVAL_THRESHOLDS, enforce_screening_budget, evaluation_passes, policy_for,
    record_screening_usage, retention_cutoff, select_model_variant, usage_for,
)
from .operations import (
    alert_dict, evaluate_canary, evaluate_slo_alerts, readiness_report, slo_policy_dict,
    run_operational_sweep, schedule_operational_sweep, slo_policy_for, tenant_operations_report,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_seed:
        seed()
    if settings.master_seed_on_start and has_master_seed_config():
        try:
            user = create_master_user()
            seed(user["id"])
            print(f"Master account ready: {user['email']} ({user['id']})")
        except Exception as exc:
            print(f"Master account seed skipped: {exc}")
    schedule_operational_sweep(initial=True)
    yield


settings = get_settings()
app = FastAPI(title="TalentFlow AI API", version="0.11.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error": {"code": f"HTTP_{exc.status_code}", "message": exc.detail}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed",
            "error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "issues": jsonable_encoder(exc.errors())},
        },
    )

SAMPLE_JD = """Tuyển Backend Python Developer. Yêu cầu Python, FastAPI, PostgreSQL và REST API.
Ít nhất 2 năm kinh nghiệm. Ưu tiên Docker, Redis."""
SAMPLE_CV = """Nguyễn Minh Anh — Backend Engineer
minhanh@example.com
3 năm kinh nghiệm phát triển backend bằng Python.
Xây dựng REST API và microservices sử dụng FastAPI.
Thiết kế PostgreSQL. Triển khai Docker và làm việc với Redis."""


class JobCreate(BaseModel):
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)
    department: str = "Engineering"
    location: str = "Remote"


class ApplicationCreate(BaseModel):
    job_id: str
    candidate_name: str
    candidate_email: str = ""
    resume_text: str = Field(min_length=20)


class ReviewCreate(BaseModel):
    decision: str
    note: str = ""


class CriteriaApproval(BaseModel):
    approved: bool = True
    note: str = ""


class ClearRecruitmentData(BaseModel):
    confirmation: str

    @field_validator("confirmation")
    @classmethod
    def require_confirmation(cls, value: str) -> str:
        if value.strip().upper() != "XOA TOAN BO":
            raise ValueError("confirmation must be XOA TOAN BO")
        return value


class CriteriaUpdate(BaseModel):
    required_skills: list[str] = Field(min_length=1, max_length=30)
    preferred_skills: list[str] = Field(default_factory=list, max_length=30)
    minimum_experience: int = Field(default=0, ge=0, le=60)
    note: str = ""


class ShortlistApproval(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=20)
    note: str = ""


class ShortlistTriggerUpdate(BaseModel):
    enabled: bool = True
    min_completed: int = Field(default=2, ge=1, le=1000)
    top_n: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=65, ge=0, le=100)


class ApprovalResolution(BaseModel):
    decision: str
    note: str = ""
    application_ids: list[str] | None = Field(default=None, max_length=20)


class BookingCreate(BaseModel):
    slot: datetime
    timezone_name: str = "Asia/Ho_Chi_Minh"
    idempotency_key: str | None = Field(default=None, max_length=200)

    @field_validator("slot")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("slot must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("timezone_name")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone_name must be a valid IANA timezone") from exc
        return value


class SchedulingInvitationCreate(BaseModel):
    timezone_name: str = "Asia/Ho_Chi_Minh"
    duration_minutes: int = Field(default=60, ge=30, le=180, multiple_of=30)
    expires_in_hours: int = Field(default=72, ge=1, le=336)

    @field_validator("timezone_name")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone_name must be a valid IANA timezone") from exc
        return value


class InterviewConfirmation(BaseModel):
    note: str = Field(default="", max_length=2000)


class EmailTemplateCreate(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9_]+$")
    subject: str = Field(min_length=2, max_length=300)
    body_text: str = Field(min_length=10, max_length=10000)


class InterviewPolicyUpdate(BaseModel):
    reminder_minutes: list[int] = Field(default_factory=lambda: [1440, 60], min_length=1, max_length=5)
    max_reschedules: int = Field(default=2, ge=0, le=10)
    feedback_due_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("reminder_minutes")
    @classmethod
    def valid_reminders(cls, value: list[int]) -> list[int]:
        if any(minutes < 15 or minutes > 10080 for minutes in value):
            raise ValueError("reminder_minutes must be between 15 and 10080")
        return sorted(set(value), reverse=True)


class ScorecardAnswer(BaseModel):
    criterion: str = Field(min_length=2, max_length=120)
    rating: int = Field(ge=1, le=5)
    evidence: str = Field(default="", max_length=2000)


class ScorecardCreate(BaseModel):
    interviewer_email: str = Field(min_length=3, max_length=320)
    answers: list[ScorecardAnswer] = Field(min_length=1, max_length=30)
    recommendation: str
    note: str = Field(default="", max_length=5000)

    @field_validator("recommendation")
    @classmethod
    def valid_recommendation(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"STRONG_YES", "YES", "MIXED", "NO", "STRONG_NO"}:
            raise ValueError("unsupported recommendation")
        return normalized


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class TenantMemberCreate(BaseModel):
    user_id: str = Field(min_length=3, max_length=128)
    role: str = "RECRUITER"

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"OWNER", "ADMIN", "RECRUITER", "VIEWER"}:
            raise ValueError("unsupported tenant role")
        return normalized


class TenantPolicyUpdate(BaseModel):
    retention_days: int = Field(default=365, ge=1, le=3650)
    monthly_screening_limit: int = Field(default=10000, ge=1, le=10000000)
    screenings_per_minute: int = Field(default=60, ge=1, le=10000)
    monthly_token_limit: int = Field(default=10000000, ge=1, le=1000000000)
    monthly_cost_limit_micros: int = Field(default=100000000, ge=1, le=1000000000000)
    email_enabled: bool = True
    calendar_enabled: bool = True
    mail_sandbox_enabled: bool = False
    mail_sandbox_base_email: str = Field(default="", max_length=320)
    mail_sandbox_max_alias: int = Field(default=100, ge=1, le=10000)


class MailSandboxUpdate(BaseModel):
    enabled: bool = True
    base_email: str = Field(default="vinhvp.khmtk36@gmail.com", min_length=3, max_length=320)
    max_alias: int = Field(default=100, ge=1, le=10000)

    @field_validator("base_email")
    @classmethod
    def valid_base_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        local, separator, domain = normalized.partition("@")
        if not separator or not local or not domain or "+" in local or " " in normalized:
            raise ValueError("base_email must be a valid address without a plus alias")
        return normalized


class MailSandboxTest(BaseModel):
    alias_number: int = Field(ge=1, le=10000)
    subject: str = Field(default="TalentFlow mail sandbox test", min_length=1, max_length=300)
    body: str = Field(default="Email thử nghiệm từ TalentFlow Mail Sandbox.", min_length=1, max_length=10000)


class ConnectorCreate(BaseModel):
    kind: str
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["resumes.write"], min_length=1, max_length=3)
    consent_basis: str = Field(min_length=5, max_length=240)
    consented_at: datetime

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"INBOX", "FOLDER", "ATS"}:
            raise ValueError("kind must be INBOX, FOLDER, or ATS")
        return normalized

    @field_validator("scopes")
    @classmethod
    def least_privilege_scopes(cls, value: list[str]) -> list[str]:
        if set(value) - {"resumes.write"}:
            raise ValueError("source connectors only support resumes.write")
        return sorted(set(value))


class ConnectorIngest(BaseModel):
    source_ref: str = Field(min_length=1, max_length=255)
    source_uri: str = Field(default="", max_length=1000)
    job_id: str
    candidate_name: str = Field(min_length=2, max_length=200)
    candidate_email: str = Field(default="", max_length=320)
    resume_text: str = Field(min_length=20, max_length=200000)
    candidate_consented_at: datetime


class ModelPolicyUpdate(BaseModel):
    champion: dict = Field(default_factory=dict)
    challenger: dict = Field(default_factory=dict)
    challenger_percent: int = Field(default=0, ge=0, le=50)

    @field_validator("champion", "challenger")
    @classmethod
    def valid_variant(cls, value: dict) -> dict:
        if set(value) - {"model", "prompt_profile"}:
            raise ValueError("variant only supports model and prompt_profile")
        profile = value.get("prompt_profile", "baseline")
        if profile not in {"baseline", "strict_evidence"}:
            raise ValueError("unsupported prompt_profile")
        model = str(value.get("model", ""))
        if len(model) > 200:
            raise ValueError("model is too long")
        return {"model": model, "prompt_profile": profile}


class OperationalSLOPolicyUpdate(BaseModel):
    window_hours: int = Field(default=24, ge=1, le=168)
    min_screening_success_rate: float = Field(default=.95, ge=0, le=1)
    max_p95_latency_ms: int = Field(default=120000, ge=100, le=3600000)
    max_due_outbox: int = Field(default=0, ge=0, le=100000)
    max_failed_outbox: int = Field(default=0, ge=0, le=100000)
    max_stale_approvals: int = Field(default=0, ge=0, le=100000)
    budget_warning_percent: int = Field(default=80, ge=1, le=100)
    min_canary_samples: int = Field(default=20, ge=1, le=1000000)
    max_success_rate_drop: float = Field(default=.02, ge=0, le=1)
    max_latency_regression_percent: int = Field(default=20, ge=0, le=1000)
    max_cost_regression_percent: int = Field(default=15, ge=0, le=1000)
    notifications_enabled: bool = False
    notification_email: str = Field(default="", max_length=320)

    @field_validator("notification_email")
    @classmethod
    def valid_notification_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized and ("@" not in normalized or normalized.startswith("@") or normalized.endswith("@")):
            raise ValueError("notification_email must be a valid email address")
        return normalized


class AlertAction(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"ACKNOWLEDGE", "RESOLVE"}:
            raise ValueError("action must be ACKNOWLEDGE or RESOLVE")
        return normalized


class CanaryMetrics(BaseModel):
    samples: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    p95_latency_ms: int = Field(ge=0)
    cost_per_screening_micros: int = Field(ge=0)
    outbox_failures: int = Field(default=0, ge=0)


class ReleaseGateCreate(BaseModel):
    release_version: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._+-]+$")
    baseline: CanaryMetrics
    candidate: CanaryMetrics


def pipeline(review_status: str = "waiting") -> list[dict]:
    completed_through = "Recruiter Review" if review_status == "completed" else "Interview Kit Generated"
    return screening_pipeline(completed_through)


def audit(db, owner_id: str, application_id: str | None, action: str, metadata: dict | None = None) -> None:
    db.add(AuditLog(owner_id=owner_id, application_id=application_id, action=action, metadata_json=metadata or {}))


def job_dict(job: Job, count: int = 0) -> dict:
    return {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
            "description": job.description, "requirements": job.requirements, "status": job.status, "applications_count": count}


def criteria_version_dict(value: CriteriaVersion) -> dict:
    return {"id": value.id, "job_id": value.job_id, "version": value.version, "criteria": value.criteria,
            "status": value.status, "parent_id": value.parent_id, "change_note": value.change_note,
            "approved_at": value.approved_at.isoformat() if value.approved_at else None,
            "created_at": value.created_at.isoformat() if value.created_at else None}


def approval_dict(value: ApprovalRequest) -> dict:
    return {"id": value.id, "type": value.request_type, "status": value.status, "job_id": value.job_id,
            "application_id": value.application_id, "resource_id": value.resource_id, "title": value.title,
            "summary": value.summary, "payload": value.payload, "resolution": value.resolution,
            "created_at": value.created_at.isoformat() if value.created_at else None}


def application_dict(item: Application) -> dict:
    screening = item.screening or {}
    if "final_score" not in screening:
        screening = {
            "final_score": 0,
            "recommendation": "Processing" if item.status == ApplicationStatus.PROCESSING.value else "Screening Failed",
            "evidence": [],
            "experience_years": 0,
        }
    return {"id": item.id, "job_id": item.job_id, "batch_id": item.batch_id,
            "candidate": {"name": item.candidate_name, "email": item.candidate_email}, "status": item.status,
            "resume_filename": item.resume_filename, "resume_size": item.resume_size,
            "screening": screening, "pipeline": item.pipeline, "review": item.review}


def batch_dict(db, batch: UploadBatch) -> dict:
    items = list(db.scalars(select(BatchItem).where(BatchItem.batch_id == batch.id).order_by(BatchItem.created_at)))
    applications = {
        item.id: item for item in db.scalars(
            select(Application).where(
                Application.owner_id == batch.owner_id,
                Application.id.in_([value.application_id for value in items if value.application_id]),
            )
        )
    } if any(value.application_id for value in items) else {}
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "total": batch.total,
        "completed": batch.completed,
        "failed": batch.failed,
        "skipped": batch.skipped,
        "processed": batch.completed + batch.failed + batch.skipped,
        "items": [
            {
                "id": item.id,
                "filename": item.filename,
                "status": item.status,
                "error": item.error,
                "task_id": item.task_id,
                "application": application_dict(applications[item.application_id]) if item.application_id in applications else None,
            }
            for item in items
        ],
    }


def shortlist_report(job: Job, applications: list[Application]) -> str:
    requirements = job.requirements or {}
    required = ", ".join(requirements.get("required_skills", []) or ["Chưa xác định"])
    preferred = ", ".join(requirements.get("preferred_skills", []) or ["Không có"])
    lines = [
        f"# Shortlist Report: {job.title}",
        "",
        f"- Department: {job.department}",
        f"- Location: {job.location}",
        f"- Required skills: {required}",
        f"- Preferred skills: {preferred}",
        f"- Minimum experience: {requirements.get('minimum_experience', 0)} năm",
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Top Candidates",
        "",
    ]
    for index, item in enumerate(applications, start=1):
        screening = item.screening or {}
        kit = screening.get("interview_kit") or {}
        if not kit:
            kit = generate_interview_kit(item.resume_text, requirements, screening, job.title)
        lines.extend([
            f"### {index}. {item.candidate_name}",
            "",
            f"- Email: {item.candidate_email or 'N/A'}",
            f"- Score: {screening.get('final_score', 0)}%",
            f"- Recommendation: {screening.get('recommendation', 'N/A')}",
            f"- Status: {item.status}",
            "",
            "Evidence:",
        ])
        for evidence in screening.get("evidence", [])[:8]:
            marker = "match" if evidence.get("matched") else "missing"
            lines.append(f"- [{marker}] {evidence.get('requirement')}: {evidence.get('evidence')}")
        lines.extend(["", "Interview questions:"])
        for question in kit.get("questions", [])[:7]:
            lines.append(f"- {question.get('type')}: {question.get('question')}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def interview_dict(item: Interview) -> dict:
    def utc_iso(value: datetime) -> str:
        normalized = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        return normalized.isoformat().replace("+00:00", "Z")

    return {"id": item.id, "application_id": item.application_id, "start_at": utc_iso(item.start_at),
            "end_at": utc_iso(item.end_at), "status": item.status, "meeting_url": item.meeting_url,
            "provider": item.provider, "external_event_id": item.external_event_id,
            "timezone": item.timezone_name, "reschedule_count": item.reschedule_count,
            "outcome": item.outcome}


def require_job(db, job_id: str, owner_id: str) -> Job:
    value = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
    if not value:
        raise HTTPException(404, "Job not found")
    return value


def require_application(db, application_id: str, owner_id: str) -> Application:
    value = db.scalar(select(Application).where(Application.id == application_id, Application.owner_id == owner_id))
    if not value:
        raise HTTPException(404, "Application not found")
    return value


async def build_application(db, owner_id: str, job: Job, name: str, email: str, text: str,
                            filename: str | None = None, size: int | None = None,
                            digest: str | None = None, batch_id: str | None = None) -> Application:
    enforce_screening_budget(db, owner_id)
    criteria = latest_criteria(db, job.id, approved_only=True) or ensure_criteria_version(db, job)
    variant, variant_config = select_model_variant(db, owner_id, digest or f"{job.id}:{email}:{name}")
    prompt_profile = str(variant_config.get("prompt_profile", "baseline"))
    model_override = str(variant_config.get("model", "")).strip() or None
    result = await screen_candidate_ai(
        text, criteria.criteria, name, email,
        model_override=model_override, prompt_profile=prompt_profile,
    )
    result["interview_kit"] = await generate_interview_kit_ai(text, criteria.criteria, result, job.title, name, email)
    result, embedding = calibrate_screening(result, text, criteria.criteria)
    item = Application(owner_id=owner_id, job_id=job.id, batch_id=batch_id, candidate_name=name,
                       candidate_email=email, resume_filename=filename, resume_size=size,
                       resume_checksum=digest, resume_text=text, screening=result, pipeline=pipeline())
    db.add(item); db.flush()
    ai = llm_config()
    used_openrouter = result.get("screening_source") != "rules"
    input_tokens = max(1, len(text) // 4) if used_openrouter else 0
    output_tokens = max(1, len(str(result)) // 4) if used_openrouter else 0
    run = AgentRun(
        owner_id=owner_id, application_id=item.id, criteria_version_id=criteria.id,
        status=RunStatus.COMPLETED.value, current_node="completed", provider="openrouter" if used_openrouter else "rules",
        model=(model_override or ai["model"]) if ai["configured"] else None,
        prompt_version=f"screening-v2:{prompt_profile}+calibration-v1",
        fallback_reason=None if used_openrouter else ("openrouter_unavailable_or_invalid_response" if ai["configured"] else "openrouter_not_configured"),
        idempotency_key=f"screening:{item.id}:criteria:{criteria.id}", started_at=utcnow(), finished_at=utcnow(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        trace_json={"model": (model_override or ai["model"]) if ai["configured"] else None,
                    "prompt_version": f"screening-v2:{prompt_profile}+calibration-v1",
                    "experiment_variant": variant,
                    "tools": ["verified_evidence", "hash_embedding", "score_calibration"],
                    "calibration_version": result["calibration_version"],
                    "fallback_reason": None if used_openrouter else ("openrouter_unavailable_or_invalid_response" if ai["configured"] else "openrouter_not_configured")},
    )
    db.add(run); db.flush()
    record_screening_usage(db, owner_id, input_tokens, output_tokens, run.cost_micros)
    record_screening_artifact(db, item, run, result, embedding)
    maybe_create_shortlist(db, job.id, owner_id, "application_created")
    audit(db, owner_id, item.id, "SCREENING_COMPLETED", {"score": result["final_score"], "run_id": run.id,
                                                          "criteria_version_id": criteria.id})
    return item


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _policy_dict(policy: TenantPolicy, usage: TenantUsage) -> dict:
    return {
        "tenant_id": policy.owner_id,
        "retention_days": policy.retention_days,
        "monthly_screening_limit": policy.monthly_screening_limit,
        "screenings_per_minute": policy.screenings_per_minute,
        "monthly_token_limit": policy.monthly_token_limit,
        "monthly_cost_limit_micros": policy.monthly_cost_limit_micros,
        "email_enabled": policy.email_enabled,
        "calendar_enabled": policy.calendar_enabled,
        "mail_sandbox_enabled": policy.mail_sandbox_enabled,
        "mail_sandbox_base_email": policy.mail_sandbox_base_email,
        "mail_sandbox_max_alias": policy.mail_sandbox_max_alias,
        "usage": {"period": usage.period, "screenings": usage.screenings,
                  "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                  "cost_micros": usage.cost_micros},
    }


def _erase_application(db, item: Application, reason: str) -> None:
    interview_ids = list(db.scalars(select(Interview.id).where(Interview.application_id == item.id)))
    run_ids = list(db.scalars(select(AgentRun.id).where(AgentRun.application_id == item.id)))
    for ingestion in db.scalars(select(SourceIngestion).where(SourceIngestion.application_id == item.id)):
        ingestion.source_ref = "deleted:" + hashlib.sha256(
            f"{ingestion.connector_id}:{ingestion.source_ref}".encode()
        ).hexdigest()
        ingestion.source_uri = ""
        ingestion.checksum = hashlib.sha256(f"deleted:{ingestion.id}".encode()).hexdigest()
        ingestion.provenance = {"redacted": True, "deleted_at": datetime.now(timezone.utc).isoformat()}
        ingestion.application_id = None
        ingestion.status = "DATA_DELETED"
    db.execute(sql_update(BatchItem).where(BatchItem.application_id == item.id).values(
        application_id=None, filename="deleted", checksum=None, error=None
    ))
    db.execute(sql_update(AuditLog).where(AuditLog.application_id == item.id).values(
        application_id=None, metadata_json={"redacted": True, "reason": reason}
    ))
    if interview_ids:
        db.execute(sql_delete(FeedbackSummary).where(FeedbackSummary.interview_id.in_(interview_ids)))
        db.execute(sql_delete(InterviewScorecard).where(InterviewScorecard.interview_id.in_(interview_ids)))
        db.execute(sql_delete(OutboxEvent).where(
            OutboxEvent.aggregate_type == "interview", OutboxEvent.aggregate_id.in_(interview_ids)
        ))
    if run_ids:
        db.execute(sql_delete(ScreeningArtifact).where(ScreeningArtifact.run_id.in_(run_ids)))
        db.execute(sql_delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
    db.execute(sql_delete(SchedulingInvitation).where(SchedulingInvitation.application_id == item.id))
    db.execute(sql_delete(ApprovalRequest).where(ApprovalRequest.application_id == item.id))
    db.execute(sql_delete(AgentTask).where(AgentTask.application_id == item.id))
    db.execute(sql_delete(AgentRun).where(AgentRun.application_id == item.id))
    db.execute(sql_delete(Interview).where(Interview.application_id == item.id))
    db.execute(sql_delete(OutboxEvent).where(
        OutboxEvent.aggregate_type == "application", OutboxEvent.aggregate_id == item.id
    ))
    audit(db, item.owner_id, None, "CANDIDATE_DATA_DELETED", {
        "application_hash": hashlib.sha256(item.id.encode()).hexdigest(), "reason": reason,
    })
    db.delete(item)


@app.post("/api/tenants", status_code=201)
def create_tenant(payload: TenantCreate, user_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        tenant = Tenant(name=payload.name, created_by=user_id)
        db.add(tenant); db.flush()
        db.add(TenantMembership(tenant_id=tenant.id, user_id=user_id, role="OWNER"))
        policy_for(db, tenant.id)
        return {"id": tenant.id, "name": tenant.name, "role": "OWNER"}


@app.get("/api/tenants")
def list_tenants(user_id: str = Depends(current_user_id)) -> list[dict]:
    with session_scope() as db:
        memberships = list(db.scalars(select(TenantMembership).where(
            TenantMembership.user_id == user_id, TenantMembership.status == "ACTIVE"
        )))
        tenants = {value.id: value for value in db.scalars(select(Tenant).where(
            Tenant.id.in_([member.tenant_id for member in memberships])
        ))} if memberships else {}
        return [{"id": user_id, "name": "Personal", "role": "OWNER"}] + [
            {"id": member.tenant_id, "name": tenants[member.tenant_id].name, "role": member.role}
            for member in memberships if member.tenant_id in tenants
        ]


@app.post("/api/tenants/{tenant_id}/members", status_code=201)
def add_tenant_member(tenant_id: str, payload: TenantMemberCreate,
                      user_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        tenant = db.get(Tenant, tenant_id)
        membership = db.scalar(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id,
            TenantMembership.status == "ACTIVE",
        ))
        if not tenant or (tenant.created_by != user_id and (not membership or membership.role not in {"OWNER", "ADMIN"})):
            raise HTTPException(403, "Tenant admin role required")
        existing = db.scalar(select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == payload.user_id
        ))
        if existing:
            existing.role = payload.role; existing.status = "ACTIVE"; member = existing
        else:
            member = TenantMembership(tenant_id=tenant_id, user_id=payload.user_id, role=payload.role)
            db.add(member); db.flush()
        return {"tenant_id": tenant_id, "user_id": member.user_id, "role": member.role, "status": member.status}


@app.get("/api/tenant-policy")
def get_tenant_policy(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        return _policy_dict(policy_for(db, owner_id), usage_for(db, owner_id))


@app.put("/api/tenant-policy")
def update_tenant_policy(payload: TenantPolicyUpdate,
                         owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        policy = policy_for(db, owner_id)
        for key, value in payload.model_dump().items():
            setattr(policy, key, value)
        audit(db, owner_id, None, "TENANT_POLICY_UPDATED", payload.model_dump())
        return _policy_dict(policy, usage_for(db, owner_id))


def _mail_sandbox_dict(policy: TenantPolicy) -> dict:
    aliases = []
    if policy.mail_sandbox_base_email:
        aliases = [mail_sandbox_alias(policy.mail_sandbox_base_email, number)
                   for number in range(1, min(policy.mail_sandbox_max_alias, 10) + 1)]
    return {
        "enabled": policy.mail_sandbox_enabled,
        "base_email": policy.mail_sandbox_base_email,
        "max_alias": policy.mail_sandbox_max_alias,
        "sample_aliases": aliases,
        "delivery_note": "Gmail delivers plus aliases to the base inbox while TalentFlow keeps the alias unchanged.",
    }


@app.get("/api/mail-sandbox")
def get_mail_sandbox(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        return _mail_sandbox_dict(policy_for(db, owner_id))


@app.put("/api/mail-sandbox")
def update_mail_sandbox(
    payload: MailSandboxUpdate,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    with session_scope() as db:
        policy = policy_for(db, owner_id)
        policy.mail_sandbox_enabled = payload.enabled
        policy.mail_sandbox_base_email = payload.base_email
        policy.mail_sandbox_max_alias = payload.max_alias
        audit(db, owner_id, None, "MAIL_SANDBOX_UPDATED", {
            "enabled": payload.enabled, "base_email": payload.base_email, "max_alias": payload.max_alias,
        })
        return _mail_sandbox_dict(policy)


@app.post("/api/mail-sandbox/test")
def send_mail_sandbox_test(
    payload: MailSandboxTest,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    with session_scope() as db:
        policy = policy_for(db, owner_id)
        if not policy.mail_sandbox_enabled or not policy.mail_sandbox_base_email:
            raise HTTPException(409, "Mail sandbox must be configured and enabled")
        if payload.alias_number > policy.mail_sandbox_max_alias:
            raise HTTPException(422, "alias_number exceeds the configured whitelist")
        recipient = mail_sandbox_alias(policy.mail_sandbox_base_email, payload.alias_number)
        event = add_outbox(
            db, owner_id=owner_id, aggregate_type="mail_sandbox", aggregate_id=str(uuid4()),
            operation="EMAIL_SEND", payload={
                "to": recipient, "subject": payload.subject, "body": payload.body, "kind": "MAIL_SANDBOX_TEST",
            }, idempotency_key=f"mail-sandbox-test:{owner_id}:{uuid4()}",
        )
        event_id = event.id
        audit(db, owner_id, None, "MAIL_SANDBOX_TEST_QUEUED", {
            "outbox_id": event_id, "recipient": recipient,
        })
    dispatch_outbox(event_id)
    with session_scope() as db:
        event = db.get(OutboxEvent, event_id)
        return {
            "outbox_id": event.id, "recipient": recipient, "stored_recipient": event.payload.get("to"),
            "status": event.status, "provider_message_id": event.provider_message_id,
            "simulated": not event.provider_message_id or event.provider_message_id.startswith("local-mail-"),
        }


@app.get("/api/source-connectors")
def list_source_connectors(owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        values = db.scalars(select(SourceConnector).where(SourceConnector.owner_id == owner_id).order_by(SourceConnector.created_at.desc()))
        return [{"id": value.id, "kind": value.kind, "name": value.name, "status": value.status,
                 "scopes": value.scopes, "consent_basis": value.consent_basis,
                 "consented_at": value.consented_at.isoformat()} for value in values]


@app.post("/api/source-connectors", status_code=201)
def create_source_connector(payload: ConnectorCreate,
                            owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    consented_at = _aware_datetime(payload.consented_at)
    if consented_at > datetime.now(timezone.utc):
        raise HTTPException(422, "consented_at cannot be in the future")
    token = secrets.token_urlsafe(32)
    with session_scope() as db:
        connector = SourceConnector(owner_id=owner_id, secret_hash=hashlib.sha256(token.encode()).hexdigest(),
                                    consented_at=consented_at, **payload.model_dump(exclude={"consented_at"}))
        db.add(connector); db.flush()
        audit(db, owner_id, None, "SOURCE_CONNECTOR_CREATED", {
            "connector_id": connector.id, "kind": connector.kind, "scopes": connector.scopes,
        })
        return {"id": connector.id, "kind": connector.kind, "name": connector.name,
                "status": connector.status, "scopes": connector.scopes, "token": token}


@app.delete("/api/source-connectors/{connector_id}")
def revoke_source_connector(connector_id: str,
                            owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        connector = db.scalar(select(SourceConnector).where(
            SourceConnector.id == connector_id, SourceConnector.owner_id == owner_id
        ))
        if not connector:
            raise HTTPException(404, "Source connector not found")
        connector.status = "REVOKED"
        audit(db, owner_id, None, "SOURCE_CONNECTOR_REVOKED", {"connector_id": connector.id})
        return {"id": connector.id, "status": connector.status}


@app.post("/api/source-connectors/{connector_id}/ingest", status_code=201)
async def ingest_from_connector(connector_id: str, payload: ConnectorIngest,
                                x_connector_token: str = Header(alias="X-Connector-Token")) -> dict:
    candidate_consented_at = _aware_datetime(payload.candidate_consented_at)
    if candidate_consented_at > datetime.now(timezone.utc):
        raise HTTPException(422, "candidate_consented_at cannot be in the future")
    with session_scope() as db:
        connector = db.get(SourceConnector, connector_id)
        supplied_hash = hashlib.sha256(x_connector_token.encode()).hexdigest()
        if not connector or connector.status != "ACTIVE" or not secrets.compare_digest(connector.secret_hash, supplied_hash):
            raise HTTPException(401, "Invalid or inactive connector token")
        if "resumes.write" not in (connector.scopes or []):
            raise HTTPException(403, "Connector lacks resumes.write scope")
        existing = db.scalar(select(SourceIngestion).where(
            SourceIngestion.connector_id == connector.id, SourceIngestion.source_ref == payload.source_ref
        ))
        if existing:
            application = db.get(Application, existing.application_id) if existing.application_id else None
            return {"ingestion_id": existing.id, "duplicate": True,
                    "application": application_dict(application) if application else None}
        job = require_job(db, payload.job_id, connector.owner_id)
        digest = hashlib.sha256(payload.resume_text.encode()).hexdigest()
        duplicate = db.scalar(select(Application).where(
            Application.owner_id == connector.owner_id, Application.job_id == job.id,
            Application.resume_checksum == digest,
        ))
        item = duplicate or await build_application(
            db, connector.owner_id, job, payload.candidate_name, payload.candidate_email,
            payload.resume_text, digest=digest,
        )
        ingestion = SourceIngestion(
            owner_id=connector.owner_id, connector_id=connector.id, application_id=item.id,
            source_ref=payload.source_ref, source_uri=payload.source_uri, checksum=digest,
            candidate_consented_at=candidate_consented_at,
            provenance={"connector_kind": connector.kind, "connector_name": connector.name,
                        "source_ref": payload.source_ref, "source_uri": payload.source_uri,
                        "received_at": datetime.now(timezone.utc).isoformat(), "candidate_consent_recorded": True},
            status="DUPLICATE" if duplicate else "COMPLETED",
        )
        db.add(ingestion); db.flush()
        audit(db, connector.owner_id, item.id, "SOURCE_CV_INGESTED", {
            "connector_id": connector.id, "ingestion_id": ingestion.id, "source_ref": payload.source_ref,
            "duplicate": bool(duplicate),
        })
        return {"ingestion_id": ingestion.id, "duplicate": bool(duplicate), "application": application_dict(item)}


@app.get("/api/applications/{application_id}/data-export")
def export_candidate_data(application_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        interviews = list(db.scalars(select(Interview).where(Interview.application_id == item.id)))
        interview_ids = [value.id for value in interviews]
        ingestions = list(db.scalars(select(SourceIngestion).where(SourceIngestion.application_id == item.id)))
        audit(db, owner_id, item.id, "CANDIDATE_DATA_EXPORTED", {"format": "json"})
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(), "tenant_id": owner_id,
            "application": {**application_dict(item), "resume_text": item.resume_text,
                            "resume_checksum": item.resume_checksum, "created_at": item.created_at.isoformat()},
            "provenance": [{"connector_id": value.connector_id, "source_ref": value.source_ref,
                            "source_uri": value.source_uri, "checksum": value.checksum,
                            "candidate_consented_at": value.candidate_consented_at.isoformat(),
                            "provenance": value.provenance} for value in ingestions],
            "interviews": [interview_dict(value) for value in interviews],
            "scorecards": [scorecard_dict(value) for value in db.scalars(select(InterviewScorecard).where(
                InterviewScorecard.interview_id.in_(interview_ids)
            ))] if interview_ids else [],
        }


@app.delete("/api/applications/{application_id}/data")
def delete_candidate_data(application_id: str,
                          owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        _erase_application(db, item, "data_subject_request")
        return {"status": "deleted", "application_id": application_id}


@app.post("/api/data-lifecycle/run-retention")
def run_retention(owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        policy = policy_for(db, owner_id)
        candidates = list(db.scalars(select(Application).where(
            Application.owner_id == owner_id,
            Application.created_at < retention_cutoff(policy),
            Application.status.in_([ApplicationStatus.REJECTED.value, ApplicationStatus.ARCHIVED.value,
                                    ApplicationStatus.SCREENING_FAILED.value]),
        )))
        ids = [item.id for item in candidates]
        for item in candidates:
            _erase_application(db, item, "retention_policy")
        audit(db, owner_id, None, "RETENTION_SWEEP_COMPLETED", {"deleted": len(ids)})
        return {"deleted": len(ids), "application_ids": ids, "retention_days": policy.retention_days}


def _model_policy_dict(value: ModelPolicy) -> dict:
    return {"id": value.id, "workflow": value.workflow, "champion": value.champion,
            "challenger": value.challenger, "challenger_percent": value.challenger_percent,
            "status": value.status, "evaluation_metrics": value.evaluation_metrics,
            "evaluated_at": value.evaluated_at.isoformat() if value.evaluated_at else None}


@app.get("/api/model-policy")
def get_model_policy(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        value = db.scalar(select(ModelPolicy).where(ModelPolicy.owner_id == owner_id,
                                                     ModelPolicy.workflow == "candidate_screening"))
        return _model_policy_dict(value) if value else {"workflow": "candidate_screening", "status": "UNCONFIGURED"}


@app.put("/api/model-policy")
def update_model_policy(payload: ModelPolicyUpdate,
                        owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        value = db.scalar(select(ModelPolicy).where(ModelPolicy.owner_id == owner_id,
                                                     ModelPolicy.workflow == "candidate_screening"))
        if not value:
            value = ModelPolicy(owner_id=owner_id, workflow="candidate_screening")
            db.add(value); db.flush()
        value.champion = payload.champion; value.challenger = payload.challenger
        value.challenger_percent = payload.challenger_percent
        value.status = "DRAFT"; value.evaluation_metrics = {}; value.evaluated_at = None
        return _model_policy_dict(value)


@app.post("/api/model-policy/evaluate")
def evaluate_model_policy(owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    from evals.run_baseline import evaluate

    metrics = evaluate()
    with session_scope() as db:
        value = db.scalar(select(ModelPolicy).where(ModelPolicy.owner_id == owner_id,
                                                     ModelPolicy.workflow == "candidate_screening"))
        if not value:
            raise HTTPException(404, "Model policy not configured")
        value.evaluation_metrics = metrics; value.evaluated_at = datetime.now(timezone.utc)
        value.status = "EVALUATED" if evaluation_passes(metrics) else "REGRESSION_FAILED"
        audit(db, owner_id, None, "MODEL_POLICY_EVALUATED", {"metrics": metrics, "thresholds": EVAL_THRESHOLDS})
        return _model_policy_dict(value)


@app.post("/api/model-policy/activate")
def activate_model_policy(owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        value = db.scalar(select(ModelPolicy).where(ModelPolicy.owner_id == owner_id,
                                                     ModelPolicy.workflow == "candidate_screening"))
        if not value or not value.evaluated_at or not evaluation_passes(value.evaluation_metrics or {}):
            raise HTTPException(409, "Passing regression evaluation is required before activation")
        value.status = "ACTIVE"
        audit(db, owner_id, None, "MODEL_POLICY_ACTIVATED", {
            "model_policy_id": value.id, "challenger_percent": value.challenger_percent,
        })
        return _model_policy_dict(value)


@app.get("/api/live")
def live() -> dict:
    return {"status": "ok", "service": "talentflow-api"}


@app.get("/api/health")
def health() -> dict:
    queue = queue_summary()
    return {"status": "ok" if queue["reachable"] else "degraded", "service": "talentflow-api", "ai": llm_config(),
            "queue": queue, "auth_required": get_settings().auth_required,
            "environment": get_settings().environment,
            "integrations": {"provider": get_settings().integration_provider,
                             "real_side_effects": get_settings().integration_provider != "local"}}


@app.get("/api/ready")
def ready(response: Response) -> dict:
    with SessionLocal() as db:
        report = readiness_report(db, get_settings(), queue_summary())
    if report["status"] != "ready":
        response.status_code = 503
    return report


@app.get("/api/operations/readiness")
def tenant_readiness(response: Response, owner_id: str = Depends(current_tenant_id)) -> dict:
    with SessionLocal() as db:
        report = readiness_report(db, get_settings(), queue_summary(), owner_id=owner_id)
    if report["status"] != "ready":
        response.status_code = 503
    return report


@app.get("/api/operations/metrics")
def operations_metrics(
    hours: int = Query(24, ge=1, le=168),
    owner_id: str = Depends(current_tenant_id),
) -> dict:
    with session_scope() as db:
        return tenant_operations_report(db, owner_id, hours=hours)


@app.get("/api/operations/slo-policy")
def get_slo_policy(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        return slo_policy_dict(slo_policy_for(db, owner_id))


@app.put("/api/operations/slo-policy")
def update_slo_policy(
    payload: OperationalSLOPolicyUpdate,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    with session_scope() as db:
        policy = slo_policy_for(db, owner_id)
        for key, value in payload.model_dump().items():
            setattr(policy, key, value)
        policy.updated_at = utcnow()
        audit(db, owner_id, None, "OPERATIONAL_SLO_POLICY_UPDATED", payload.model_dump())
        return slo_policy_dict(policy)


@app.post("/api/operations/evaluate")
def evaluate_operations(owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    with session_scope() as db:
        report, alerts = evaluate_slo_alerts(db, owner_id)
        active = [value for value in alerts if value.status != "RESOLVED"]
        audit(db, owner_id, None, "OPERATIONAL_SLO_EVALUATED", {
            "active_alerts": len(active), "signals": [value.signal for value in active],
        })
        return {"metrics": report, "alerts": [alert_dict(value) for value in alerts]}


@app.post("/api/operations/sweep")
def sweep_operations(owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN"))) -> dict:
    return run_operational_sweep(owner_id, schedule_next=False)


@app.get("/api/operations/alerts")
def list_operational_alerts(
    status: str | None = Query(None, max_length=24),
    owner_id: str = Depends(current_tenant_id),
) -> list[dict]:
    with session_scope() as db:
        statement = select(OperationalAlert).where(OperationalAlert.owner_id == owner_id)
        if status:
            statement = statement.where(OperationalAlert.status == status.upper())
        values = db.scalars(statement.order_by(OperationalAlert.last_observed_at.desc()))
        return [alert_dict(value) for value in values]


@app.post("/api/operations/alerts/{alert_id}/action")
def act_on_operational_alert(
    alert_id: str,
    payload: AlertAction,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    with session_scope() as db:
        alert = db.scalar(select(OperationalAlert).where(
            OperationalAlert.id == alert_id, OperationalAlert.owner_id == owner_id,
        ))
        if not alert:
            raise HTTPException(404, "Operational alert not found")
        now = utcnow()
        if payload.action == "ACKNOWLEDGE":
            if alert.status == "RESOLVED":
                raise HTTPException(409, "Resolved alert cannot be acknowledged")
            alert.status = "ACKNOWLEDGED"
            alert.acknowledged_at = now
        else:
            alert.status = "RESOLVED"
            alert.resolved_at = now
        audit(db, owner_id, None, f"OPERATIONAL_ALERT_{payload.action}D", {
            "alert_id": alert.id, "signal": alert.signal,
        })
        return alert_dict(alert)


def _release_gate_dict(value: ReleaseGate) -> dict:
    return {
        "id": value.id, "release_version": value.release_version, "status": value.status,
        "baseline": value.baseline, "candidate": value.candidate, "reasons": value.reasons,
        "evaluated_at": value.evaluated_at.replace(tzinfo=value.evaluated_at.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(),
        "promoted_at": value.promoted_at.replace(tzinfo=value.promoted_at.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat() if value.promoted_at else None,
        "promoted_by": value.promoted_by,
    }


@app.post("/api/operations/release-gates", status_code=201)
def create_release_gate(
    payload: ReleaseGateCreate,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    with session_scope() as db:
        existing = db.scalar(select(ReleaseGate).where(
            ReleaseGate.owner_id == owner_id, ReleaseGate.release_version == payload.release_version,
        ))
        if existing:
            return _release_gate_dict(existing)
        policy = slo_policy_for(db, owner_id)
        baseline = payload.baseline.model_dump()
        candidate = payload.candidate.model_dump()
        result = evaluate_canary(baseline, candidate, policy)
        gate = ReleaseGate(
            owner_id=owner_id, release_version=payload.release_version, status=result["status"],
            baseline=baseline, candidate=candidate, reasons=result["reasons"],
        )
        db.add(gate)
        db.flush()
        audit(db, owner_id, None, "RELEASE_GATE_EVALUATED", {
            "release_gate_id": gate.id, "release_version": gate.release_version,
            "status": gate.status, "reasons": gate.reasons,
        })
        return _release_gate_dict(gate)


@app.get("/api/operations/release-gates")
def list_release_gates(owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        values = db.scalars(select(ReleaseGate).where(
            ReleaseGate.owner_id == owner_id
        ).order_by(ReleaseGate.evaluated_at.desc()).limit(100))
        return [_release_gate_dict(value) for value in values]


@app.post("/api/operations/release-gates/{gate_id}/promote")
def promote_release_gate(
    gate_id: str,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
    user_id: str = Depends(current_user_id),
) -> dict:
    with session_scope() as db:
        gate = db.scalar(select(ReleaseGate).where(ReleaseGate.id == gate_id, ReleaseGate.owner_id == owner_id))
        if not gate:
            raise HTTPException(404, "Release gate not found")
        if gate.status == "PROMOTED":
            return _release_gate_dict(gate)
        if gate.status != "PROMOTION_ALLOWED":
            raise HTTPException(409, "Release promotion is blocked by canary evaluation")
        active_critical = db.scalar(select(func.count()).select_from(OperationalAlert).where(
            OperationalAlert.owner_id == owner_id,
            OperationalAlert.severity == "CRITICAL",
            OperationalAlert.status.in_(["OPEN", "ACKNOWLEDGED"]),
        )) or 0
        if active_critical:
            raise HTTPException(409, "Release promotion is blocked by active critical operational alerts")
        readiness = readiness_report(db, get_settings(), queue_summary(), owner_id=owner_id)
        if readiness["status"] != "ready":
            raise HTTPException(409, "Release promotion is blocked because the tenant is not ready")
        gate.status = "PROMOTED"
        gate.promoted_at = utcnow()
        gate.promoted_by = user_id
        audit(db, owner_id, None, "RELEASE_PROMOTED", {
            "release_gate_id": gate.id, "release_version": gate.release_version, "promoted_by": user_id,
        })
        return _release_gate_dict(gate)


@app.get("/api/dashboard")
def dashboard(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        jobs = list(db.scalars(select(Job).where(Job.owner_id == owner_id).order_by(Job.created_at.desc())))
        apps = list(db.scalars(select(Application).where(Application.owner_id == owner_id).order_by(Application.created_at.desc())))
        interviews = list(db.scalars(select(Interview).where(Interview.owner_id == owner_id).order_by(Interview.start_at)))
        counts = dict(db.execute(select(Application.job_id, func.count()).where(Application.owner_id == owner_id).group_by(Application.job_id)).all())
        ranked = sorted(apps, key=lambda a: a.screening.get("final_score", 0), reverse=True)
        return {"metrics": {"open_jobs": len(jobs), "candidates": len(apps),
                            "awaiting_review": sum(a.status == ApplicationStatus.WAITING_REVIEW.value for a in apps), "interviews": len(interviews)},
                "applications": [application_dict(a) for a in ranked], "jobs": [job_dict(j, counts.get(j.id, 0)) for j in jobs],
                "interviews": [interview_dict(i) for i in interviews]}


@app.post("/api/data/clear")
def clear_recruitment_data(
    payload: ClearRecruitmentData,
    owner_id: str = Depends(require_tenant_role("OWNER", "ADMIN")),
) -> dict:
    """Remove recruitment records for one tenant while preserving its account and settings."""
    with session_scope() as db:
        counts = {
            "jobs": db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == owner_id)) or 0,
            "applications": db.scalar(select(func.count()).select_from(Application).where(Application.owner_id == owner_id)) or 0,
            "interviews": db.scalar(select(func.count()).select_from(Interview).where(Interview.owner_id == owner_id)) or 0,
        }
        # Delete in dependency order so the operation behaves consistently in SQLite and PostgreSQL.
        for model in (
            FeedbackSummary, InterviewScorecard, OutboxEvent, SchedulingInvitation, Interview,
            ScreeningArtifact, AgentTask, AgentStep, AgentRun, BatchItem, ShortlistProposal,
            ApprovalRequest, CriteriaVersion, UploadBatch, Application, Job, AuditLog,
        ):
            db.execute(sql_delete(model).where(model.owner_id == owner_id))
        audit(db, owner_id, None, "RECRUITMENT_DATA_CLEARED", counts)
        return {"status": "cleared", "removed": counts}


@app.get("/api/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: JobStatus | None = None,
    q: str | None = Query(None, max_length=200),
    owner_id: str = Depends(current_tenant_id),
) -> list[dict]:
    with session_scope() as db:
        statement = select(Job).where(Job.owner_id == owner_id)
        if status:
            statement = statement.where(Job.status == status.value)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            statement = statement.where(or_(Job.title.ilike(pattern), Job.department.ilike(pattern), Job.location.ilike(pattern)))
        statement = statement.order_by(Job.created_at.desc()).offset(offset).limit(limit)
        return [job_dict(j) for j in db.scalars(statement)]


@app.get("/api/applications")
def list_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: ApplicationStatus | None = None,
    job_id: str | None = None,
    q: str | None = Query(None, max_length=200),
    owner_id: str = Depends(current_tenant_id),
) -> list[dict]:
    with session_scope() as db:
        statement = select(Application).where(Application.owner_id == owner_id)
        if status:
            statement = statement.where(Application.status == status.value)
        if job_id:
            statement = statement.where(Application.job_id == job_id)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            statement = statement.where(or_(Application.candidate_name.ilike(pattern), Application.candidate_email.ilike(pattern)))
        statement = statement.order_by(Application.created_at.desc()).offset(offset).limit(limit)
        return [application_dict(item) for item in db.scalars(statement)]


@app.post("/api/jobs", status_code=201)
async def create_job(payload: JobCreate, owner_id: str = Depends(current_tenant_id)) -> dict:
    requirements = await extract_requirements_ai(payload.description)
    requirements = {**requirements, "approval": {"status": "PENDING", "note": ""},
                    "shortlist_trigger": {"enabled": True, "min_completed": 2, "top_n": 5, "min_score": 65}}
    with session_scope() as db:
        job = Job(owner_id=owner_id, **payload.model_dump(), requirements=requirements)
        db.add(job); db.flush()
        create_criteria_version(db, job, owner_id, requirements, "Extracted from job description")
        return job_dict(job)


@app.put("/api/jobs/{job_id}/criteria")
def update_criteria(job_id: str, payload: CriteriaUpdate, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        criteria = payload.model_dump(exclude={"note"})
        version = create_criteria_version(db, job, owner_id, criteria, payload.note)
        job.requirements = {**criteria, "approval": {"status": "PENDING", "note": payload.note},
                            "shortlist_trigger": (job.requirements or {}).get("shortlist_trigger", {})}
        audit(db, owner_id, None, "CRITERIA_VERSION_CREATED",
              {"job_id": job.id, "criteria_version_id": version.id, "version": version.version})
        return {"job": job_dict(job), "criteria_version": criteria_version_dict(version)}


def _queue_rescreens(db, job: Job, criteria: CriteriaVersion, owner_id: str) -> list[str]:
    task_ids: list[str] = []
    applications = list(db.scalars(select(Application).where(
        Application.job_id == job.id, Application.owner_id == owner_id
    )))
    for item in applications:
        existing = db.scalar(select(AgentRun).where(
            AgentRun.application_id == item.id, AgentRun.criteria_version_id == criteria.id
        ))
        if existing:
            continue
        parent = db.scalar(select(AgentRun).where(
            AgentRun.application_id == item.id
        ).order_by(AgentRun.created_at.desc()).limit(1))
        ai = llm_config()
        run = AgentRun(
            owner_id=owner_id, application_id=item.id, criteria_version_id=criteria.id,
            parent_run_id=parent.id if parent else None, workflow="candidate_rescreen", trigger="criteria_approved",
            status=RunStatus.QUEUED.value, current_node="rescreen_queued",
            provider="openrouter" if ai["configured"] else "rules", model=ai["model"] if ai["configured"] else None,
            prompt_version="screening-v2+calibration-v1",
            fallback_reason=None if ai["configured"] else "openrouter_not_configured",
            idempotency_key=f"rescreen:{item.id}:criteria:{criteria.id}",
        )
        db.add(run); db.flush()
        task = AgentTask(
            owner_id=owner_id, application_id=item.id, run_id=run.id, status=TaskStatus.QUEUED.value,
            max_attempts=get_settings().task_max_attempts,
            idempotency_key=f"rescreen-task:{item.id}:criteria:{criteria.id}",
        )
        db.add(task); db.flush()
        item.status = ApplicationStatus.PROCESSING.value
        item.pipeline = screening_pipeline("Candidate Extracted")
        task_ids.append(task.id)
        audit(db, owner_id, item.id, "RESCREEN_QUEUED", {"run_id": run.id, "criteria_version_id": criteria.id})
    return task_ids


@app.post("/api/jobs/{job_id}/approve-criteria")
async def approve_criteria(job_id: str, payload: CriteriaApproval, owner_id: str = Depends(current_tenant_id)) -> dict:
    task_ids: list[str] = []
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        criteria = ensure_criteria_version(db, job)
        criteria.status = "APPROVED" if payload.approved else "NEEDS_REVISION"
        criteria.approved_at = utcnow() if payload.approved else None
        job.requirements = {
            **(job.requirements or {}), **criteria.criteria,
            "approval": {
                "status": "APPROVED" if payload.approved else "NEEDS_REVISION",
                "note": payload.note,
                "approved_at": datetime.now(timezone.utc).isoformat() if payload.approved else None,
            },
        }
        audit(db, owner_id, None, "CRITERIA_APPROVED" if payload.approved else "CRITERIA_REVISION_REQUESTED",
              {"job_id": job.id, "criteria_version_id": criteria.id, "version": criteria.version, "note": payload.note})
        request = db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.resource_id == criteria.id, ApprovalRequest.status == "PENDING"
        ))
        if request:
            request.status = criteria.status
            request.resolution = {"note": payload.note}
            request.decided_at = utcnow()
        if payload.approved:
            task_ids = _queue_rescreens(db, job, criteria, owner_id)
        response = job_dict(job)
    for task_id in task_ids:
        try:
            await enqueue_screening_async(task_id)
        except Exception as exc:
            mark_enqueue_failed(task_id, str(exc))
    return response


@app.get("/api/jobs/{job_id}/shortlist")
def shortlist(job_id: str, limit: int = 5, owner_id: str = Depends(current_tenant_id)) -> dict:
    limit = min(max(limit, 1), 20)
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        apps = [item for item in db.scalars(select(Application).where(
            Application.job_id == job.id, Application.owner_id == owner_id
        )) if "final_score" in (item.screening or {})]
        ranked = sorted(apps, key=lambda a: a.screening.get("final_score", 0), reverse=True)
        return {
            "job": job_dict(job, len(apps)),
            "limit": limit,
            "items": [application_dict(item) for item in ranked[:limit]],
            "shortlist_approval": (job.requirements or {}).get("shortlist_approval", {"status": "PENDING", "application_ids": []}),
        }


@app.put("/api/jobs/{job_id}/shortlist-trigger")
def update_shortlist_trigger(job_id: str, payload: ShortlistTriggerUpdate,
                             owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        job.requirements = {**(job.requirements or {}), "shortlist_trigger": payload.model_dump()}
        audit(db, owner_id, None, "SHORTLIST_TRIGGER_UPDATED", {"job_id": job.id, **payload.model_dump()})
        return job_dict(job)


@app.get("/api/jobs/{job_id}/criteria-versions")
def criteria_versions(job_id: str, owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        require_job(db, job_id, owner_id)
        values = db.scalars(select(CriteriaVersion).where(
            CriteriaVersion.job_id == job_id, CriteriaVersion.owner_id == owner_id
        ).order_by(CriteriaVersion.version.desc()))
        return [criteria_version_dict(value) for value in values]


@app.post("/api/jobs/{job_id}/shortlist-proposals", status_code=201)
def create_shortlist_proposal(job_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        require_job(db, job_id, owner_id)
        proposal = maybe_create_shortlist(db, job_id, owner_id, "manual_trigger")
        if not proposal:
            raise HTTPException(409, "Shortlist trigger conditions are not met or criteria are not approved")
        return {"id": proposal.id, "status": proposal.status, "application_ids": proposal.application_ids,
                "ranking": proposal.ranking, "criteria_version_id": proposal.criteria_version_id,
                "trigger": proposal.trigger}


@app.get("/api/jobs/{job_id}/shortlist-proposals")
def list_shortlist_proposals(job_id: str, owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        require_job(db, job_id, owner_id)
        values = db.scalars(select(ShortlistProposal).where(
            ShortlistProposal.job_id == job_id, ShortlistProposal.owner_id == owner_id
        ).order_by(ShortlistProposal.created_at.desc()))
        return [{"id": value.id, "status": value.status, "application_ids": value.application_ids,
                 "ranking": value.ranking, "criteria_version_id": value.criteria_version_id,
                 "trigger": value.trigger, "decision_note": value.decision_note} for value in values]


@app.get("/api/jobs/{job_id}/shortlist-report")
def export_shortlist_report(job_id: str, limit: int = 5, owner_id: str = Depends(current_tenant_id)) -> Response:
    limit = min(max(limit, 1), 20)
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        apps = [item for item in db.scalars(select(Application).where(
            Application.job_id == job.id, Application.owner_id == owner_id
        )) if "final_score" in (item.screening or {})]
        approved_ids = (job.requirements or {}).get("shortlist_approval", {}).get("application_ids") or []
        if approved_ids:
            approved = set(approved_ids)
            ranked = sorted([item for item in apps if item.id in approved], key=lambda a: approved_ids.index(a.id))
        else:
            ranked = sorted(apps, key=lambda a: a.screening.get("final_score", 0), reverse=True)[:limit]
        audit(db, owner_id, None, "SHORTLIST_REPORT_EXPORTED", {"job_id": job.id, "count": len(ranked)})
        filename = f"{job.title.lower().replace(' ', '-')[:48]}-shortlist.md"
        return Response(
            shortlist_report(job, ranked),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@app.post("/api/jobs/{job_id}/approve-shortlist")
def approve_shortlist(job_id: str, payload: ShortlistApproval, owner_id: str = Depends(current_tenant_id)) -> dict:
    selected = set(payload.application_ids)
    invitations: list[dict] = []
    outbox_ids: list[str] = []
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        criteria = latest_criteria(db, job.id, approved_only=True)
        if not criteria:
            raise HTTPException(409, "Criteria must be approved before shortlist approval")
        apps = [item for item in db.scalars(select(Application).where(
            Application.job_id == job.id, Application.owner_id == owner_id
        )) if "final_score" in (item.screening or {})]
        known = {item.id for item in apps}
        missing = selected - known
        if missing:
            raise HTTPException(422, "Shortlist contains applications outside this job")
        for item in apps:
            if item.id in selected and item.status == ApplicationStatus.WAITING_REVIEW.value:
                item.status = ApplicationStatus.SHORTLISTED.value
        job.requirements = {
            **(job.requirements or {}),
            "shortlist_approval": {
                "status": "APPROVED",
                "application_ids": payload.application_ids,
                "note": payload.note,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        audit(db, owner_id, None, "SHORTLIST_APPROVED", {"job_id": job.id, "application_ids": payload.application_ids, "note": payload.note})
        proposal = db.scalar(select(ShortlistProposal).where(
            ShortlistProposal.job_id == job.id, ShortlistProposal.status == "PENDING"
        ).order_by(ShortlistProposal.created_at.desc()).limit(1))
        if proposal:
            proposal.status = "APPROVED"
            proposal.decision_note = payload.note
            proposal.decided_at = utcnow()
            request = db.scalar(select(ApprovalRequest).where(
                ApprovalRequest.resource_id == proposal.id, ApprovalRequest.status == "PENDING"
            ))
            if request:
                request.status = "APPROVED"
                request.resolution = {"note": payload.note, "application_ids": payload.application_ids}
                request.decided_at = utcnow()
        ranked = sorted([item for item in apps if item.id in selected], key=lambda a: a.screening.get("final_score", 0), reverse=True)
        invitation_payload = SchedulingInvitationCreate()
        for item in ranked:
            if not item.candidate_email:
                continue
            existing = db.scalar(select(SchedulingInvitation).where(
                SchedulingInvitation.application_id == item.id,
                SchedulingInvitation.owner_id == owner_id,
                SchedulingInvitation.purpose == "SCHEDULE",
                SchedulingInvitation.status == "ACTIVE",
            ))
            if existing:
                continue
            invitation, outbox_id = _create_scheduling_invitation(db, item, invitation_payload)
            invitations.append(invitation)
            outbox_ids.append(outbox_id)
        response = {"job": job_dict(job, len(apps)), "items": [application_dict(item) for item in ranked],
                    "invitations": invitations}
    for outbox_id in outbox_ids:
        dispatch_outbox(outbox_id)
    for invitation in invitations:
        schedule_follow_up_sweep(owner_id, datetime.fromisoformat(invitation["expires_at"]), f"invitation:{invitation['id']}")
    return response


@app.get("/api/approvals")
def approval_inbox(
    status: str = Query("PENDING", max_length=32),
    request_type: str | None = Query(None, max_length=40),
    limit: int = Query(100, ge=1, le=200),
    owner_id: str = Depends(current_tenant_id),
) -> list[dict]:
    with session_scope() as db:
        statement = select(ApprovalRequest).where(
            ApprovalRequest.owner_id == owner_id, ApprovalRequest.status == status.upper()
        )
        if request_type:
            statement = statement.where(ApprovalRequest.request_type == request_type.upper())
        values = db.scalars(statement.order_by(ApprovalRequest.created_at.desc()).limit(limit))
        return [approval_dict(value) for value in values]


@app.post("/api/approvals/{approval_id}/resolve")
async def resolve_approval(approval_id: str, payload: ApprovalResolution,
                           owner_id: str = Depends(current_tenant_id)) -> dict:
    decision = payload.decision.upper()
    if decision not in {"APPROVE", "REJECT"}:
        raise HTTPException(422, "decision must be APPROVE or REJECT")
    task_ids: list[str] = []
    invitation_outbox_ids: list[str] = []
    invitations: list[dict] = []
    with session_scope() as db:
        request = db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id, ApprovalRequest.owner_id == owner_id
        ))
        if not request:
            raise HTTPException(404, "Approval request not found")
        if request.status != "PENDING":
            raise HTTPException(409, "Approval request is already resolved")
        request.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
        request.resolution = {"note": payload.note, "application_ids": payload.application_ids or []}
        request.decided_at = utcnow()
        if request.request_type == "CRITERIA":
            criteria = db.scalar(select(CriteriaVersion).where(
                CriteriaVersion.id == request.resource_id, CriteriaVersion.owner_id == owner_id
            ))
            job = require_job(db, request.job_id or "", owner_id)
            if not criteria:
                raise HTTPException(409, "Criteria version no longer exists")
            criteria.status = "APPROVED" if decision == "APPROVE" else "NEEDS_REVISION"
            criteria.approved_at = utcnow() if decision == "APPROVE" else None
            job.requirements = {**(job.requirements or {}), **criteria.criteria,
                                "approval": {"status": criteria.status, "note": payload.note}}
            if decision == "APPROVE":
                task_ids = _queue_rescreens(db, job, criteria, owner_id)
        elif request.request_type == "SHORTLIST":
            proposal = db.scalar(select(ShortlistProposal).where(
                ShortlistProposal.id == request.resource_id, ShortlistProposal.owner_id == owner_id
            ))
            if not proposal:
                raise HTTPException(409, "Shortlist proposal no longer exists")
            proposal.status = request.status
            proposal.decision_note = payload.note
            proposal.decided_at = utcnow()
            if decision == "APPROVE":
                selected = payload.application_ids or proposal.application_ids
                apps = list(db.scalars(select(Application).where(
                    Application.job_id == proposal.job_id, Application.owner_id == owner_id
                )))
                if set(selected) - {item.id for item in apps}:
                    raise HTTPException(422, "Shortlist contains applications outside this job")
                for item in apps:
                    if item.id in selected and item.status == ApplicationStatus.WAITING_REVIEW.value:
                        item.status = ApplicationStatus.SHORTLISTED.value
                job = require_job(db, proposal.job_id, owner_id)
                job.requirements = {**(job.requirements or {}), "shortlist_approval": {
                    "status": "APPROVED", "application_ids": selected, "note": payload.note,
                    "approved_at": utcnow().isoformat(), "proposal_id": proposal.id,
                }}
                invitation_payload = SchedulingInvitationCreate()
                for item in apps:
                    if item.id not in selected or not item.candidate_email:
                        continue
                    existing = db.scalar(select(SchedulingInvitation).where(
                        SchedulingInvitation.application_id == item.id,
                        SchedulingInvitation.owner_id == owner_id,
                        SchedulingInvitation.purpose == "SCHEDULE",
                        SchedulingInvitation.status == "ACTIVE",
                    ))
                    if existing:
                        continue
                    invitation, outbox_id = _create_scheduling_invitation(db, item, invitation_payload)
                    invitations.append(invitation)
                    invitation_outbox_ids.append(outbox_id)
        audit(db, owner_id, request.application_id, f"{request.request_type}_{request.status}",
              {"approval_id": request.id, "resource_id": request.resource_id, "note": payload.note})
        response = approval_dict(request)
    for task_id in task_ids:
        try:
            await enqueue_screening_async(task_id)
        except Exception as exc:
            mark_enqueue_failed(task_id, str(exc))
    for outbox_id in invitation_outbox_ids:
        dispatch_outbox(outbox_id)
    for invitation in invitations:
        schedule_follow_up_sweep(owner_id, datetime.fromisoformat(invitation["expires_at"]), f"invitation:{invitation['id']}")
    return response


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        linked = list(db.scalars(select(Application).where(Application.job_id == job_id, Application.owner_id == owner_id)))
        if any(a.status not in {ApplicationStatus.REJECTED.value, ApplicationStatus.ARCHIVED.value} for a in linked):
            raise HTTPException(409, "Only jobs with no candidates or rejected/archived candidates can be deleted")
        db.delete(job)
        return {"status": "deleted", "job_id": job_id, "removed_applications": len(linked)}


@app.post("/api/applications", status_code=201)
async def create_application(payload: ApplicationCreate, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        item = await build_application(db, owner_id, require_job(db, payload.job_id, owner_id), payload.candidate_name,
                                       payload.candidate_email, payload.resume_text)
        return application_dict(item)


@app.get("/api/applications/{application_id}/interview-kit")
async def get_interview_kit(application_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        if item.status in {ApplicationStatus.PROCESSING.value, ApplicationStatus.SCREENING_FAILED.value}:
            raise HTTPException(409, "Application screening is not complete")
        job = require_job(db, item.job_id, owner_id)
        kit = (item.screening or {}).get("interview_kit")
        if not kit:
            kit = await generate_interview_kit_ai(item.resume_text, job.requirements, item.screening, job.title,
                                                  item.candidate_name, item.candidate_email)
            item.screening = {**(item.screening or {}), "interview_kit": kit}
            audit(db, owner_id, item.id, "INTERVIEW_KIT_GENERATED", {"source": kit.get("source", "unknown")})
        return kit


@app.post("/api/application-batches", status_code=202)
async def create_batch(job_id: str = Form(...), files: list[UploadFile] = File(...), owner_id: str = Depends(current_tenant_id)) -> dict:
    if not 1 <= len(files) <= 20:
        raise HTTPException(422, "Mỗi batch phải có từ 1 đến 20 CV")
    task_ids: list[str] = []
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        criteria = latest_criteria(db, job.id, approved_only=True) or ensure_criteria_version(db, job)
        batch = UploadBatch(owner_id=owner_id, job_id=job_id, total=len(files)); db.add(batch); db.flush()
        for file in files:
            filename = (file.filename or "unnamed")[:255]
            try:
                content, text = await extract_resume(file)
                digest = checksum(content)
                duplicate = db.scalar(select(Application).where(
                    Application.owner_id == owner_id,
                    Application.job_id == job_id,
                    Application.resume_checksum == digest,
                ))
                if duplicate:
                    db.add(BatchItem(owner_id=owner_id, batch_id=batch.id, application_id=duplicate.id,
                                     filename=filename, checksum=digest, status=BatchItemStatus.DUPLICATE.value,
                                     error="CV trùng checksum trong cùng job"))
                    batch.skipped += 1
                    audit(db, owner_id, duplicate.id, "CV_DUPLICATE_SKIPPED", {"filename": filename, "checksum": digest})
                    continue

                name, email = candidate_identity(text, filename)
                application_id = str(uuid5(NAMESPACE_URL, f"talentflow:{owner_id}:{job_id}:{digest}"))
                application = Application(
                    id=application_id,
                    owner_id=owner_id,
                    job_id=job.id,
                    batch_id=batch.id,
                    candidate_name=name,
                    candidate_email=email,
                    status=ApplicationStatus.PROCESSING.value,
                    resume_filename=filename,
                    resume_size=len(content),
                    resume_checksum=digest,
                    resume_text=text,
                    screening={},
                    pipeline=screening_pipeline("Candidate Extracted"),
                )
                try:
                    with db.begin_nested():
                        db.add(application)
                        db.flush()
                except IntegrityError:
                    duplicate = db.scalar(select(Application).where(
                        Application.owner_id == owner_id,
                        Application.job_id == job_id,
                        Application.resume_checksum == digest,
                    ))
                    if not duplicate:
                        raise
                    db.add(BatchItem(owner_id=owner_id, batch_id=batch.id, application_id=duplicate.id,
                                     filename=filename, checksum=digest, status=BatchItemStatus.DUPLICATE.value,
                                     error="CV trùng checksum trong cùng job"))
                    batch.skipped += 1
                    audit(db, owner_id, duplicate.id, "CV_DUPLICATE_SKIPPED",
                          {"filename": filename, "checksum": digest, "reason": "concurrent_insert"})
                    continue
                batch_item = BatchItem(owner_id=owner_id, batch_id=batch.id, application_id=application.id,
                                       filename=filename, checksum=digest, status=BatchItemStatus.QUEUED.value)
                db.add(batch_item); db.flush()
                ai = llm_config()
                run = AgentRun(owner_id=owner_id, application_id=application.id, status=RunStatus.QUEUED.value,
                               criteria_version_id=criteria.id,
                               provider="openrouter" if ai["configured"] else "rules",
                               model=ai["model"] if ai["configured"] else None,
                               prompt_version="screening-v1+interview-kit-v1",
                               fallback_reason=None if ai["configured"] else "openrouter_not_configured",
                               idempotency_key=f"screening:{application.id}:v1")
                db.add(run); db.flush()
                task = AgentTask(owner_id=owner_id, application_id=application.id, batch_id=batch.id,
                                 batch_item_id=batch_item.id, run_id=run.id, status=TaskStatus.QUEUED.value,
                                 max_attempts=get_settings().task_max_attempts,
                                 idempotency_key=f"screening-task:{application.id}:v1")
                db.add(task); db.flush()
                batch_item.task_id = task.id
                task_ids.append(task.id)
                audit(db, owner_id, application.id, "CV_EXTRACTED",
                      {"filename": filename, "size": len(content), "checksum": digest, "stored_original": False})
                audit(db, owner_id, application.id, "SCREENING_QUEUED", {"task_id": task.id, "run_id": run.id})
            except HTTPException as exc:
                batch.failed += 1
                db.add(BatchItem(owner_id=owner_id, batch_id=batch.id, filename=filename,
                                 status=BatchItemStatus.FAILED.value, error=str(exc.detail)))
            except Exception as exc:
                batch.failed += 1
                db.add(BatchItem(owner_id=owner_id, batch_id=batch.id, filename=filename,
                                 status=BatchItemStatus.FAILED.value,
                                 error=f"Không thể chuẩn bị CV: {str(exc)[:500]}"))
        terminal = batch.failed + batch.skipped
        if terminal == batch.total:
            batch.status = BatchStatus.FAILED.value if batch.failed and not batch.skipped else BatchStatus.PARTIAL.value if batch.failed else BatchStatus.COMPLETED.value
        batch_id = batch.id

    for task_id in task_ids:
        try:
            await enqueue_screening_async(task_id)
        except Exception as exc:
            mark_enqueue_failed(task_id, str(exc))

    with session_scope() as db:
        return batch_dict(db, db.get(UploadBatch, batch_id))


@app.get("/api/application-batches/{batch_id}")
def get_batch(batch_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.owner_id == owner_id))
        if not batch:
            raise HTTPException(404, "Batch not found")
        return batch_dict(db, batch)


@app.post("/api/application-batches/{batch_id}/items/{item_id}/retry", status_code=202)
async def retry_batch_item(batch_id: str, item_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        item = db.scalar(select(BatchItem).where(
            BatchItem.id == item_id, BatchItem.batch_id == batch_id, BatchItem.owner_id == owner_id
        ))
        if not item:
            raise HTTPException(404, "Batch item not found")
        if item.status != BatchItemStatus.FAILED.value or not item.task_id:
            raise HTTPException(409, "Only failed screening items can be retried")
        previous_task = db.get(AgentTask, item.task_id)
        application = db.get(Application, item.application_id) if item.application_id else None
        if not all((previous_task, application)):
            raise HTTPException(409, "Failed item has no retryable screening task")
        retry_id = str(uuid4())
        ai = llm_config()
        run = AgentRun(owner_id=owner_id, application_id=application.id, trigger="manual_retry",
                       status=RunStatus.QUEUED.value,
                       provider="openrouter" if ai["configured"] else "rules",
                       model=ai["model"] if ai["configured"] else None,
                       prompt_version="screening-v1+interview-kit-v1",
                       fallback_reason=None if ai["configured"] else "openrouter_not_configured",
                       current_node="manual_retry_queued",
                       idempotency_key=f"screening:{application.id}:retry:{retry_id}")
        db.add(run); db.flush()
        task = AgentTask(owner_id=owner_id, application_id=application.id, batch_id=batch_id,
                         batch_item_id=item.id, run_id=run.id, status=TaskStatus.QUEUED.value,
                         max_attempts=get_settings().task_max_attempts,
                         idempotency_key=f"screening-task:{application.id}:retry:{retry_id}")
        db.add(task); db.flush()
        item.task_id = task.id
        application.status = ApplicationStatus.PROCESSING.value
        application.pipeline = screening_pipeline("Candidate Extracted")
        item.status = BatchItemStatus.QUEUED.value
        item.error = None
        batch = db.get(UploadBatch, batch_id)
        if batch:
            batch.failed = max(0, batch.failed - 1)
            batch.status = BatchStatus.PROCESSING.value
        audit(db, owner_id, application.id, "SCREENING_RETRY_REQUESTED",
              {"task_id": task.id, "previous_task_id": previous_task.id, "run_id": run.id})
        task_id = task.id
    try:
        await enqueue_screening_async(task_id)
    except Exception as exc:
        mark_enqueue_failed(task_id, str(exc))
        raise HTTPException(503, f"Queue unavailable: {str(exc)[:500]}") from exc
    with session_scope() as db:
        return batch_dict(db, db.get(UploadBatch, batch_id))


@app.get("/api/agent-runs/{run_id}")
def get_agent_run(run_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == owner_id))
        if not run:
            raise HTTPException(404, "Agent run not found")
        steps = list(db.scalars(select(AgentStep).where(
            AgentStep.run_id == run.id, AgentStep.owner_id == owner_id
        ).order_by(AgentStep.created_at)))
        return {
            "id": run.id,
            "application_id": run.application_id,
            "workflow": run.workflow,
            "trigger": run.trigger,
            "status": run.status,
            "current_node": run.current_node,
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "fallback_reason": run.fallback_reason,
            "criteria_version_id": run.criteria_version_id,
            "parent_run_id": run.parent_run_id,
            "usage": {"input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
                      "cost_micros": run.cost_micros},
            "trace": run.trace_json,
            "error": run.error,
            "steps": [{"node": step.node, "status": step.status, "attempt": step.attempt,
                       "latency_ms": step.latency_ms, "error": step.error, "metadata": step.metadata_json}
                      for step in steps],
        }


@app.post("/api/applications/{application_id}/review")
def review(application_id: str, payload: ReviewCreate, owner_id: str = Depends(current_tenant_id)) -> dict:
    statuses = {
        ReviewDecision.INTERVIEW.value: ApplicationStatus.INTERVIEW_PENDING.value,
        ReviewDecision.MANUAL_REVIEW.value: ApplicationStatus.REVIEWED.value,
        ReviewDecision.REJECT.value: ApplicationStatus.REJECTED.value,
        ReviewDecision.ARCHIVE.value: ApplicationStatus.ARCHIVED.value,
    }
    decision = payload.decision.upper()
    if decision not in statuses:
        raise HTTPException(422, "Unsupported decision")
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        if item.status in {ApplicationStatus.PROCESSING.value, ApplicationStatus.SCREENING_FAILED.value}:
            raise HTTPException(409, "Application screening is not ready for review")
        item.status = statuses[decision]; item.review = payload.model_dump(); item.pipeline = pipeline("completed")
        audit(db, owner_id, item.id, "RECRUITER_REVIEWED", payload.model_dump())
        return application_dict(item)


@app.get("/api/interviewers/{interviewer_id}/available-slots")
def available_slots(interviewer_id: str, owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        candidates = available_slots_for_owner(db, owner_id)
    return [{"start_at": slot.isoformat(), "duration_minutes": 60} for slot in candidates]


@app.get("/api/integrations")
def list_integrations(owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        values = db.scalars(select(IntegrationConnection).where(IntegrationConnection.owner_id == owner_id))
        return [{"provider": value.provider, "status": value.status, "account_email": value.account_email,
                 "scopes": value.scopes, "expires_at": value.expires_at.isoformat() if value.expires_at else None}
                for value in values]


@app.post("/api/integrations/{provider}/authorize")
def start_integration(provider: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    try:
        return {"authorization_url": authorization_url(provider.lower(), owner_id)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/integrations/{provider}/callback")
def integration_callback(provider: str, code: str, state: str) -> RedirectResponse:
    provider = provider.lower()
    try:
        owner_id = decode_oauth_state(state, provider)
        save_connection(owner_id, provider, exchange_code(provider, code))
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"OAuth callback failed: {exc}") from exc
    return RedirectResponse(f"{get_settings().public_app_url}/?integration={provider}&status=connected")


@app.delete("/api/integrations/{provider}", status_code=204)
def revoke_integration(provider: str, owner_id: str = Depends(current_tenant_id)) -> Response:
    with session_scope() as db:
        value = db.scalar(select(IntegrationConnection).where(
            IntegrationConnection.owner_id == owner_id,
            IntegrationConnection.provider == provider.lower(),
        ))
        if not value:
            raise HTTPException(404, "Integration not found")
        value.status = "REVOKED"
        value.revoked_at = utcnow()
        value.access_token_encrypted = ""
        value.refresh_token_encrypted = ""
        audit(db, owner_id, None, "INTEGRATION_REVOKED", {"provider": provider.lower()})
    return Response(status_code=204)


@app.get("/api/email-templates")
def list_email_templates(owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        values = db.scalars(select(EmailTemplate).where(EmailTemplate.owner_id == owner_id)
                            .order_by(EmailTemplate.key, EmailTemplate.version.desc()))
        return [{"id": value.id, "key": value.key, "version": value.version, "subject": value.subject,
                 "body_text": value.body_text, "active": value.active} for value in values]


@app.post("/api/email-templates", status_code=201)
def create_email_template(payload: EmailTemplateCreate, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        current = db.scalar(select(func.max(EmailTemplate.version)).where(
            EmailTemplate.owner_id == owner_id, EmailTemplate.key == payload.key,
        )) or 0
        for old in db.scalars(select(EmailTemplate).where(
            EmailTemplate.owner_id == owner_id, EmailTemplate.key == payload.key, EmailTemplate.active.is_(True),
        )):
            old.active = False
        value = EmailTemplate(owner_id=owner_id, key=payload.key, version=current + 1,
                              subject=payload.subject, body_text=payload.body_text, active=True)
        db.add(value); db.flush()
        return {"id": value.id, "key": value.key, "version": value.version,
                "subject": value.subject, "body_text": value.body_text, "active": value.active}


def policy_dict(value: InterviewPolicy) -> dict:
    return {"reminder_minutes": value.reminder_minutes, "max_reschedules": value.max_reschedules,
            "feedback_due_hours": value.feedback_due_hours}


@app.get("/api/interview-policy")
def read_interview_policy(owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        return policy_dict(get_policy(db, owner_id))


@app.put("/api/interview-policy")
def update_interview_policy(payload: InterviewPolicyUpdate, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        value = get_policy(db, owner_id)
        value.reminder_minutes = payload.reminder_minutes
        value.max_reschedules = payload.max_reschedules
        value.feedback_due_hours = payload.feedback_due_hours
        audit(db, owner_id, None, "INTERVIEW_POLICY_UPDATED", policy_dict(value))
        return policy_dict(value)


def scorecard_dict(value: InterviewScorecard) -> dict:
    return {"id": value.id, "interview_id": value.interview_id,
            "interviewer_email": value.interviewer_email, "rubric": value.rubric,
            "answers": value.answers, "recommendation": value.recommendation, "note": value.note,
            "submitted_at": value.submitted_at.isoformat()}


def feedback_dict(value: FeedbackSummary | None) -> dict | None:
    if not value:
        return None
    return {"id": value.id, "interview_id": value.interview_id, "version": value.version,
            "summary": value.summary, "strengths": value.strengths, "concerns": value.concerns,
            "conflicts": value.conflicts, "sources": value.sources, "created_at": value.created_at.isoformat()}


@app.get("/api/interviews/{interview_id}/operations")
def interview_operations(interview_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        interview = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner_id == owner_id))
        if not interview:
            raise HTTPException(404, "Interview not found")
        cards = list(db.scalars(select(InterviewScorecard).where(
            InterviewScorecard.interview_id == interview.id, InterviewScorecard.owner_id == owner_id,
        ).order_by(InterviewScorecard.submitted_at)))
        summary = db.scalar(select(FeedbackSummary).where(
            FeedbackSummary.interview_id == interview.id, FeedbackSummary.owner_id == owner_id,
        ).order_by(FeedbackSummary.version.desc()))
        reminders = list(db.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_id == interview.id,
            OutboxEvent.idempotency_key.like(f"interview-reminder:{interview.id}:%"),
        ).order_by(OutboxEvent.available_at)))
        return {"interview": interview_dict(interview), "policy": policy_dict(get_policy(db, owner_id)),
                "scorecards": [scorecard_dict(card) for card in cards], "feedback_summary": feedback_dict(summary),
                "reminders": [{"id": event.id, "status": event.status,
                               "due_at": event.available_at.isoformat()} for event in reminders]}


@app.post("/api/interviews/{interview_id}/scorecards", status_code=201)
def submit_scorecard(interview_id: str, payload: ScorecardCreate,
                     owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        interview = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner_id == owner_id))
        if not interview:
            raise HTTPException(404, "Interview not found")
        existing = db.scalar(select(InterviewScorecard).where(
            InterviewScorecard.interview_id == interview.id,
            InterviewScorecard.interviewer_email == payload.interviewer_email.lower(),
        ))
        if existing:
            raise HTTPException(409, "Interviewer already submitted a scorecard")
        application = db.get(Application, interview.application_id)
        rubric = ((application.screening or {}).get("interview_kit") or {}).get("rubric", []) if application else []
        card = InterviewScorecard(owner_id=owner_id, interview_id=interview.id,
                                  interviewer_email=payload.interviewer_email.lower(), rubric=rubric,
                                  answers=[answer.model_dump() for answer in payload.answers],
                                  recommendation=payload.recommendation, note=payload.note)
        db.add(card); db.flush()
        interview.status = "FEEDBACK_PENDING"
        summary = summarize_feedback(db, interview)
        audit(db, owner_id, interview.application_id, "SCORECARD_SUBMITTED",
              {"interview_id": interview.id, "scorecard_id": card.id})
        return {"scorecard": scorecard_dict(card), "feedback_summary": feedback_dict(summary)}


@app.post("/api/interviews/{interview_id}/no-show")
def mark_no_show(interview_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        interview = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner_id == owner_id))
        if not interview:
            raise HTTPException(404, "Interview not found")
        interview.status = "NO_SHOW"
        interview.outcome = "NO_SHOW"
        request = escalation(db, interview, "NO_SHOW", "Ứng viên không tham dự; recruiter cần chọn liên hệ lại hoặc đóng quy trình.")
        audit(db, owner_id, interview.application_id, "INTERVIEW_NO_SHOW", {"interview_id": interview.id})
        return {"interview": interview_dict(interview), "approval": approval_dict(request)}


@app.post("/api/interview-operations/run-due")
def run_due_interview_operations(owner_id: str = Depends(current_tenant_id)) -> dict:
    result = run_follow_up_cycle(owner_id)
    result["requested_by"] = owner_id
    return result


def _create_scheduling_invitation(db, item: Application, payload: SchedulingInvitationCreate) -> tuple[dict, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    invitation = SchedulingInvitation(
        owner_id=item.owner_id, application_id=item.id, token_hash=token_hash,
        timezone_name=payload.timezone_name, duration_minutes=payload.duration_minutes,
        expires_at=utcnow() + timedelta(hours=payload.expires_in_hours),
    )
    db.add(invitation); db.flush()
    public_url = f"{get_settings().public_app_url}/?schedule={raw_token}"
    rendered = render_template(
        db, item.owner_id, "scheduling_invitation", "Mời chọn lịch phỏng vấn",
        "Chào {candidate_name},\n\nBạn đã vào danh sách phỏng vấn. Vui lòng chọn lịch tại: {public_url}\n"
        "Liên kết hết hạn lúc {expires_at}. Lịch bạn chọn sẽ được giữ riêng và chờ HR xác nhận.",
        {"candidate_name": item.candidate_name, "public_url": public_url,
         "expires_at": invitation.expires_at.isoformat()},
    )
    outbox = add_outbox(
        db, owner_id=item.owner_id, aggregate_type="scheduling_invitation", aggregate_id=invitation.id,
        operation="EMAIL_SEND", idempotency_key=f"scheduling-invitation:{invitation.id}", payload={
            "to": item.candidate_email,
            **rendered,
        },
    )
    item.status = ApplicationStatus.INTERVIEW_PENDING.value
    audit(db, item.owner_id, item.id, "SCHEDULING_INVITATION_CREATED", {"invitation_id": invitation.id})
    return ({"id": invitation.id, "status": invitation.status, "public_url": public_url,
             "expires_at": invitation.expires_at.isoformat(), "delivery_status": outbox.status}, outbox.id)


@app.post("/api/applications/{application_id}/scheduling-invitations", status_code=201)
def create_scheduling_invitation(application_id: str, payload: SchedulingInvitationCreate,
                                 owner_id: str = Depends(current_tenant_id)) -> dict:
    outbox_id = ""
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        if not item.candidate_email:
            raise HTTPException(409, "Candidate email is required")
        response, outbox_id = _create_scheduling_invitation(db, item, payload)
    dispatch_outbox(outbox_id)
    schedule_follow_up_sweep(owner_id, datetime.fromisoformat(response["expires_at"]), f"invitation:{response['id']}")
    return response


def _public_invitation(db, token: str) -> SchedulingInvitation:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    value = db.scalar(select(SchedulingInvitation).where(SchedulingInvitation.token_hash == token_hash))
    expires = value.expires_at.replace(tzinfo=value.expires_at.tzinfo or timezone.utc) if value else None
    if not value or value.status != "ACTIVE" or not expires or expires <= utcnow():
        raise HTTPException(404, "Scheduling link is invalid or expired")
    return value


@app.get("/api/public/scheduling/{token}")
def public_scheduling(token: str) -> dict:
    with session_scope() as db:
        invitation = _public_invitation(db, token)
        item = db.get(Application, invitation.application_id)
        job = db.get(Job, item.job_id) if item else None
        if not item or not job:
            raise HTTPException(404, "Scheduling invitation is no longer available")
        slots = available_slots_for_owner(db, invitation.owner_id, invitation.duration_minutes)
        interview = db.scalar(select(Interview).where(
            Interview.application_id == invitation.application_id,
            Interview.owner_id == invitation.owner_id,
            Interview.status != InterviewStatus.CANCELLED.value,
        ).order_by(Interview.updated_at.desc())) if invitation.purpose == "RESCHEDULE" else None
        return {"candidate_name": item.candidate_name, "job_title": job.title,
                "timezone": invitation.timezone_name, "duration_minutes": invitation.duration_minutes,
                "expires_at": invitation.expires_at.isoformat(),
                "mode": "reschedule" if interview else "schedule",
                "interview": interview_dict(interview) if interview else None,
                "slots": [{"start_at": slot.isoformat(), "duration_minutes": invitation.duration_minutes} for slot in slots]}


@app.post("/api/public/scheduling/{token}", status_code=201)
def book_public_interview(token: str, payload: BookingCreate) -> dict:
    outbox_id = ""
    blocked_reason = ""
    with session_scope() as db:
        invitation = _public_invitation(db, token)
        item = db.get(Application, invitation.application_id)
        if not item:
            raise HTTPException(404, "Application not found")
        offered = available_slots_for_owner(db, invitation.owner_id, invitation.duration_minutes)
        if payload.slot not in offered:
            raise HTTPException(409, "Slot is no longer available")
        current = db.scalar(select(Interview).where(
            Interview.application_id == invitation.application_id,
            Interview.owner_id == invitation.owner_id,
            Interview.status != InterviewStatus.CANCELLED.value,
        ).order_by(Interview.updated_at.desc())) if invitation.purpose == "RESCHEDULE" else None
        if current:
            policy = get_policy(db, invitation.owner_id)
            if current.reschedule_count >= policy.max_reschedules:
                escalation(db, current, "RESCHEDULE_LIMIT",
                           "Ứng viên đã vượt số lần đổi lịch trong policy; recruiter cần xử lý.",
                           {"max_reschedules": policy.max_reschedules})
                blocked_reason = "Reschedule limit reached; recruiter has been notified"
                response = interview_dict(current)
            else:
                conflict = db.scalar(select(Interview).where(
                    Interview.owner_id == invitation.owner_id, Interview.start_at == payload.slot,
                    Interview.id != current.id, Interview.status != InterviewStatus.CANCELLED.value,
                ))
                if conflict:
                    raise HTTPException(409, "Slot is no longer available")
                current.start_at = payload.slot
                current.end_at = payload.slot + timedelta(minutes=invitation.duration_minutes)
                current.timezone_name = payload.timezone_name
                current.reschedule_count += 1
                current.status = "RESCHEDULING"
                key = payload.idempotency_key or f"candidate-calendar-update:{current.id}:{payload.slot.isoformat()}"
                outbox = add_outbox(db, owner_id=invitation.owner_id, aggregate_type="interview",
                                    aggregate_id=current.id, operation="CALENDAR_UPDATE",
                                    idempotency_key=key, payload={})
                outbox_id = outbox.id
                audit(db, invitation.owner_id, item.id, "CANDIDATE_RESCHEDULE_REQUESTED",
                      {"interview_id": current.id, "reschedule_count": current.reschedule_count})
                response = interview_dict(current)
        else:
            idem = payload.idempotency_key or f"public-booking:{invitation.id}:{payload.slot.isoformat()}"
            existing = db.scalar(select(Interview).where(Interview.idempotency_key == idem))
            if existing:
                return interview_dict(existing)
            value = Interview(owner_id=invitation.owner_id, application_id=item.id, start_at=payload.slot,
                              end_at=payload.slot + timedelta(minutes=invitation.duration_minutes),
                              status=InterviewStatus.PENDING_CONFIRMATION.value, meeting_url="", timezone_name=payload.timezone_name,
                              provider=get_settings().integration_provider, idempotency_key=idem)
            db.add(value)
            try:
                db.flush()
            except IntegrityError:
                raise HTTPException(409, "Slot is no longer available") from None
            invitation.status = "USED"; invitation.selected_at = utcnow()
            item.status = ApplicationStatus.INTERVIEW_PENDING.value
            audit(db, invitation.owner_id, item.id, "INTERVIEW_SLOT_HELD", {"interview_id": value.id})
            response = interview_dict(value)
    if blocked_reason:
        raise HTTPException(409, blocked_reason)
    if outbox_id:
        dispatch_outbox(outbox_id)
    with session_scope() as db:
        return interview_dict(db.get(Interview, response["id"]))


@app.post("/api/interviews/{interview_id}/confirm")
def confirm_interview(interview_id: str, payload: InterviewConfirmation,
                      owner_id: str = Depends(current_tenant_id)) -> dict:
    outbox_id = ""
    with session_scope() as db:
        value = db.scalar(select(Interview).where(
            Interview.id == interview_id, Interview.owner_id == owner_id,
        ))
        if not value:
            raise HTTPException(404, "Interview not found")
        if value.status == InterviewStatus.SCHEDULED.value:
            return interview_dict(value)
        if value.status != InterviewStatus.PENDING_CONFIRMATION.value:
            raise HTTPException(409, "Only a candidate-held slot can be confirmed")
        application = require_application(db, value.application_id, owner_id)
        value.status = "PENDING_EXTERNAL"
        outbox = add_outbox(
            db, owner_id=owner_id, aggregate_type="interview", aggregate_id=value.id,
            operation="CALENDAR_CREATE", idempotency_key=f"calendar-confirm:{value.id}", payload={},
        )
        outbox_id = outbox.id
        audit(db, owner_id, application.id, "INTERVIEW_CONFIRMED_BY_HR", {
            "interview_id": value.id, "note": payload.note,
        })
        response = interview_dict(value)
    dispatch_outbox(outbox_id)
    with session_scope() as db:
        return interview_dict(db.get(Interview, response["id"]))


@app.post("/api/applications/{application_id}/interview", status_code=201)
def book_interview(application_id: str, payload: BookingCreate, owner_id: str = Depends(current_tenant_id)) -> dict:
    outbox_id = ""
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        idem = payload.idempotency_key or f"recruiter-booking:{item.id}:{payload.slot.isoformat()}"
        existing = db.scalar(select(Interview).where(Interview.idempotency_key == idem))
        if existing:
            return interview_dict(existing)
        if db.scalar(select(Interview).where(Interview.owner_id == owner_id, Interview.start_at == payload.slot)):
            raise HTTPException(409, "Slot is no longer available")
        value = Interview(owner_id=owner_id, application_id=item.id, start_at=payload.slot,
                          end_at=payload.slot + timedelta(hours=1), status="PENDING_EXTERNAL", meeting_url="",
                          provider=get_settings().integration_provider, timezone_name=payload.timezone_name,
                          idempotency_key=idem)
        db.add(value); db.flush()
        outbox = add_outbox(db, owner_id=owner_id, aggregate_type="interview", aggregate_id=value.id,
                            operation="CALENDAR_CREATE", idempotency_key=f"calendar-create:{idem}", payload={})
        outbox_id = outbox.id
        item.status = ApplicationStatus.INTERVIEW_PENDING.value
        audit(db, owner_id, item.id, "INTERVIEW_BOOKING_REQUESTED", {"interview_id": value.id})
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "Slot is no longer available") from None
        response = interview_dict(value)
    dispatch_outbox(outbox_id)
    with session_scope() as db:
        booked = db.get(Interview, response["id"])
        if booked and booked.status == InterviewStatus.SCHEDULED.value:
            application = db.get(Application, booked.application_id)
            if application:
                application.status = ApplicationStatus.INTERVIEW_SCHEDULED.value
        return interview_dict(booked)


@app.put("/api/interviews/{interview_id}")
def reschedule_interview(interview_id: str, payload: BookingCreate,
                         owner_id: str = Depends(current_tenant_id)) -> dict:
    outbox_id = ""
    blocked = False
    with session_scope() as db:
        value = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner_id == owner_id))
        if not value:
            raise HTTPException(404, "Interview not found")
        if value.status == InterviewStatus.CANCELLED.value:
            raise HTTPException(409, "Cancelled interview cannot be rescheduled")
        policy = get_policy(db, owner_id)
        if value.reschedule_count >= policy.max_reschedules:
            escalation(db, value, "RESCHEDULE_LIMIT", "Đã vượt số lần đổi lịch trong policy; cần xử lý ngoại lệ.",
                       {"max_reschedules": policy.max_reschedules})
            blocked = True
        else:
            conflict = db.scalar(select(Interview).where(
                Interview.owner_id == owner_id, Interview.start_at == payload.slot, Interview.id != value.id,
                Interview.status != InterviewStatus.CANCELLED.value,
            ))
            if conflict:
                raise HTTPException(409, "Slot is no longer available")
            value.start_at = payload.slot
            value.end_at = payload.slot + timedelta(hours=1)
            value.timezone_name = payload.timezone_name
            value.reschedule_count += 1
            value.status = "RESCHEDULING"
            key = payload.idempotency_key or f"calendar-update:{value.id}:{payload.slot.isoformat()}"
            outbox = add_outbox(db, owner_id=owner_id, aggregate_type="interview", aggregate_id=value.id,
                                operation="CALENDAR_UPDATE", idempotency_key=key, payload={})
            outbox_id = outbox.id
            audit(db, owner_id, value.application_id, "INTERVIEW_RESCHEDULE_REQUESTED", {"interview_id": value.id})
            response = interview_dict(value)
    if blocked:
        raise HTTPException(409, "Reschedule limit reached; escalation created")
    dispatch_outbox(outbox_id)
    with session_scope() as db:
        return interview_dict(db.get(Interview, response["id"]))


@app.delete("/api/interviews/{interview_id}")
def cancel_interview(interview_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    outbox_id = ""
    with session_scope() as db:
        value = db.scalar(select(Interview).where(Interview.id == interview_id, Interview.owner_id == owner_id))
        if not value:
            raise HTTPException(404, "Interview not found")
        if value.status == InterviewStatus.CANCELLED.value:
            return interview_dict(value)
        value.status = "CANCELLING"
        outbox = add_outbox(db, owner_id=owner_id, aggregate_type="interview", aggregate_id=value.id,
                            operation="CALENDAR_CANCEL", idempotency_key=f"calendar-cancel:{value.id}", payload={})
        outbox_id = outbox.id
        audit(db, owner_id, value.application_id, "INTERVIEW_CANCEL_REQUESTED", {"interview_id": value.id})
        response = interview_dict(value)
    dispatch_outbox(outbox_id)
    with session_scope() as db:
        return interview_dict(db.get(Interview, response["id"]))


@app.get("/api/outbox")
def list_outbox(status: str | None = None, owner_id: str = Depends(current_tenant_id)) -> list[dict]:
    with session_scope() as db:
        statement = select(OutboxEvent).where(OutboxEvent.owner_id == owner_id)
        if status:
            statement = statement.where(OutboxEvent.status == status.upper())
        values = db.scalars(statement.order_by(OutboxEvent.created_at.desc()).limit(100))
        return [{"id": value.id, "operation": value.operation, "status": value.status,
                 "attempts": value.attempts, "last_error": value.last_error,
                 "provider_id": value.provider_message_id, "created_at": value.created_at.isoformat()}
                for value in values]


@app.post("/api/outbox/{event_id}/retry")
def retry_outbox(event_id: str, owner_id: str = Depends(current_tenant_id)) -> dict:
    with session_scope() as db:
        value = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id, OutboxEvent.owner_id == owner_id))
        if not value:
            raise HTTPException(404, "Outbox event not found")
        if value.status == "COMPLETED":
            return {"id": value.id, "status": value.status}
        value.status = "PENDING"
        value.available_at = utcnow()
    dispatch_outbox(event_id)
    with session_scope() as db:
        value = db.get(OutboxEvent, event_id)
        return {"id": value.id, "status": value.status, "attempts": value.attempts, "last_error": value.last_error}


@app.post("/api/webhooks/{provider}")
async def provider_webhook(provider: str, request: Request) -> dict:
    settings = get_settings()
    validation_token = request.query_params.get("validationToken")
    if provider.lower() == "microsoft" and validation_token:
        return Response(content=validation_token, media_type="text/plain")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if settings.provider_webhook_secret:
        generic_secret = request.headers.get("x-talentflow-webhook-secret")
        google_secret = request.headers.get("x-goog-channel-token")
        microsoft_secrets = {str(item.get("clientState", "")) for item in payload.get("value", [])}
        if settings.provider_webhook_secret not in {generic_secret, google_secret, *microsoft_secrets}:
            raise HTTPException(401, "Invalid webhook secret")
    google_message = request.headers.get("x-goog-message-number")
    google_channel = request.headers.get("x-goog-channel-id", "")
    external_id = str(payload.get("id") or (f"{google_channel}:{google_message}" if google_message else "") or uuid4())
    event_type = str(payload.get("type") or payload.get("status") or request.headers.get("x-goog-resource-state") or "provider.updated")
    with session_scope() as db:
        existing = db.scalar(select(ProviderWebhookEvent).where(
            ProviderWebhookEvent.provider == provider.lower(), ProviderWebhookEvent.external_id == external_id,
        ))
        if existing:
            return {"accepted": True, "duplicate": True}
        event = ProviderWebhookEvent(provider=provider.lower(), external_id=external_id,
                                     event_type=event_type, payload=payload, processed=True)
        db.add(event)
        provider_object_id = str(payload.get("event_id") or payload.get("calendar_event_id") or "")
        interview = db.scalar(select(Interview).where(Interview.external_event_id == provider_object_id)) if provider_object_id else None
        if interview and event_type.lower() in {"cancelled", "canceled", "event.cancelled"}:
            interview.status = InterviewStatus.CANCELLED.value
            audit(db, interview.owner_id, interview.application_id, "INTERVIEW_CANCELLED_BY_PROVIDER",
                  {"provider": provider.lower(), "webhook_id": external_id})
        normalized_type = event_type.lower()
        message_id = str(payload.get("message_id") or payload.get("provider_message_id") or "")
        email_outbox = db.scalar(select(OutboxEvent).where(
            OutboxEvent.provider_message_id == message_id
        )) if message_id else None
        related_interview = (
            db.get(Interview, email_outbox.aggregate_id)
            if email_outbox and email_outbox.aggregate_type == "interview" else interview
        )
        related_invitation = (
            db.get(SchedulingInvitation, email_outbox.aggregate_id)
            if email_outbox and email_outbox.aggregate_type == "scheduling_invitation" else None
        )
        if related_interview and normalized_type in {"bounce", "bounced", "email.bounced", "delivery.failed"}:
            escalation(db, related_interview, "EMAIL_BOUNCE",
                       "Email gửi cho ứng viên bị bounce; recruiter cần kiểm tra địa chỉ hoặc kênh liên hệ khác.",
                       {"provider": provider.lower(), "webhook_id": external_id, "message_id": message_id})
            audit(db, related_interview.owner_id, related_interview.application_id, "EMAIL_BOUNCED",
                  {"provider": provider.lower(), "webhook_id": external_id})
        elif related_invitation and normalized_type in {"bounce", "bounced", "email.bounced", "delivery.failed"}:
            application = db.get(Application, related_invitation.application_id)
            dedupe_key = f"scheduling-escalation:{related_invitation.id}:EMAIL_BOUNCE"
            if not db.scalar(select(ApprovalRequest).where(ApprovalRequest.dedupe_key == dedupe_key)):
                db.add(ApprovalRequest(
                    owner_id=related_invitation.owner_id, request_type="ESCALATION", status="PENDING",
                    job_id=application.job_id if application else None,
                    application_id=related_invitation.application_id, resource_id=related_invitation.id,
                    title="Ngoại lệ phỏng vấn: email bounce",
                    summary="Email mời chọn lịch bị bounce; recruiter cần kiểm tra địa chỉ hoặc kênh liên hệ khác.",
                    payload={"reason": "EMAIL_BOUNCE", "provider": provider.lower(), "webhook_id": external_id},
                    dedupe_key=dedupe_key,
                ))
        if related_interview and normalized_type in {"reply.out_of_scope", "content.out_of_scope"}:
            escalation(db, related_interview, "OUT_OF_SCOPE_REPLY",
                       "Phản hồi nằm ngoài policy tự động; recruiter cần trả lời trực tiếp.",
                       {"provider": provider.lower(), "webhook_id": external_id})
    return {"accepted": True, "duplicate": False}


@app.get("/api/audit-logs")
def logs(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=80),
    application_id: str | None = None,
    owner_id: str = Depends(current_tenant_id),
) -> list[dict]:
    with session_scope() as db:
        statement = select(AuditLog).where(AuditLog.owner_id == owner_id)
        if action:
            statement = statement.where(AuditLog.action == action)
        if application_id:
            statement = statement.where(AuditLog.application_id == application_id)
        statement = statement.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
        values = db.scalars(statement)
        return [{"id": v.id, "application_id": v.application_id, "action": v.action,
                 "metadata": v.metadata_json,
                 "created_at": v.created_at.replace(tzinfo=v.created_at.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
                for v in values]


def seed(owner_id: str = "00000000-0000-0000-0000-000000000001") -> None:
    with session_scope() as db:
        if db.scalar(select(Job).where(Job.owner_id == owner_id)):
            return
        job = Job(id="job-backend-01", owner_id=owner_id, title="Backend Python Developer", department="Engineering",
                  location="Hồ Chí Minh · Hybrid", description=SAMPLE_JD,
                  requirements={**extract_requirements(SAMPLE_JD), "approval": {"status": "APPROVED", "note": "Seed data"},
                                "shortlist_trigger": {"enabled": True, "min_completed": 2, "top_n": 5, "min_score": 65}})
        db.add(job); db.flush()
        criteria = ensure_criteria_version(db, job)
        screening = screen_candidate(SAMPLE_CV, job.requirements)
        screening, _embedding = calibrate_screening(screening, SAMPLE_CV, criteria.criteria)
        screening["interview_kit"] = generate_interview_kit(SAMPLE_CV, job.requirements, screening, job.title)
        db.add(Application(id="app-001", owner_id=owner_id, job_id=job.id, candidate_name="Nguyễn Minh Anh",
                           candidate_email="minhanh@example.com", resume_text=SAMPLE_CV,
                           screening=screening, pipeline=pipeline()))
