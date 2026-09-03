from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from .auth import current_user_id
from .config import get_settings
from .database import session_scope
from .llm import extract_requirements_ai, generate_interview_kit_ai, llm_config, screen_candidate_ai
from .master_seed import create_master_user, has_master_seed_config
from .models import AgentRun, AgentStep, AgentTask, Application, AuditLog, BatchItem, Interview, Job, UploadBatch
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
    yield


settings = get_settings()
app = FastAPI(title="TalentFlow AI API", version="0.3.0", lifespan=lifespan)
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


class ShortlistApproval(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=20)
    note: str = ""


class BookingCreate(BaseModel):
    slot: datetime

    @field_validator("slot")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("slot must include a timezone")
        return value.astimezone(timezone.utc)


def pipeline(review_status: str = "waiting") -> list[dict]:
    completed_through = "Recruiter Review" if review_status == "completed" else "Interview Kit Generated"
    return screening_pipeline(completed_through)


def audit(db, owner_id: str, application_id: str | None, action: str, metadata: dict | None = None) -> None:
    db.add(AuditLog(owner_id=owner_id, application_id=application_id, action=action, metadata_json=metadata or {}))


def job_dict(job: Job, count: int = 0) -> dict:
    return {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
            "description": job.description, "requirements": job.requirements, "status": job.status, "applications_count": count}


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
            "end_at": utc_iso(item.end_at), "status": item.status, "meeting_url": item.meeting_url}


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
    result = await screen_candidate_ai(text, job.requirements, name, email)
    result["interview_kit"] = await generate_interview_kit_ai(text, job.requirements, result, job.title, name, email)
    item = Application(owner_id=owner_id, job_id=job.id, batch_id=batch_id, candidate_name=name,
                       candidate_email=email, resume_filename=filename, resume_size=size,
                       resume_checksum=digest, resume_text=text, screening=result, pipeline=pipeline())
    db.add(item); db.flush()
    audit(db, owner_id, item.id, "SCREENING_COMPLETED", {"score": result["final_score"], "pipeline_version": "v2"})
    return item


@app.get("/api/health")
def health() -> dict:
    queue = queue_summary()
    return {"status": "ok" if queue["reachable"] else "degraded", "service": "talentflow-api", "ai": llm_config(),
            "queue": queue, "auth_required": get_settings().auth_required,
            "environment": get_settings().environment}


@app.get("/api/dashboard")
def dashboard(owner_id: str = Depends(current_user_id)) -> dict:
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


@app.get("/api/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: JobStatus | None = None,
    q: str | None = Query(None, max_length=200),
    owner_id: str = Depends(current_user_id),
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
    owner_id: str = Depends(current_user_id),
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
async def create_job(payload: JobCreate, owner_id: str = Depends(current_user_id)) -> dict:
    requirements = await extract_requirements_ai(payload.description)
    requirements = {**requirements, "approval": {"status": "PENDING", "note": ""}}
    with session_scope() as db:
        job = Job(owner_id=owner_id, **payload.model_dump(), requirements=requirements)
        db.add(job); db.flush()
        return job_dict(job)


@app.post("/api/jobs/{job_id}/approve-criteria")
def approve_criteria(job_id: str, payload: CriteriaApproval, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        job.requirements = {
            **(job.requirements or {}),
            "approval": {
                "status": "APPROVED" if payload.approved else "NEEDS_REVISION",
                "note": payload.note,
                "approved_at": datetime.now(timezone.utc).isoformat() if payload.approved else None,
            },
        }
        audit(db, owner_id, None, "CRITERIA_APPROVED" if payload.approved else "CRITERIA_REVISION_REQUESTED",
              {"job_id": job.id, "note": payload.note})
        return job_dict(job)


@app.get("/api/jobs/{job_id}/shortlist")
def shortlist(job_id: str, limit: int = 5, owner_id: str = Depends(current_user_id)) -> dict:
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


@app.get("/api/jobs/{job_id}/shortlist-report")
def export_shortlist_report(job_id: str, limit: int = 5, owner_id: str = Depends(current_user_id)) -> Response:
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
def approve_shortlist(job_id: str, payload: ShortlistApproval, owner_id: str = Depends(current_user_id)) -> dict:
    selected = set(payload.application_ids)
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
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
        ranked = sorted([item for item in apps if item.id in selected], key=lambda a: a.screening.get("final_score", 0), reverse=True)
        return {"job": job_dict(job, len(apps)), "items": [application_dict(item) for item in ranked]}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        linked = list(db.scalars(select(Application).where(Application.job_id == job_id, Application.owner_id == owner_id)))
        if any(a.status not in {ApplicationStatus.REJECTED.value, ApplicationStatus.ARCHIVED.value} for a in linked):
            raise HTTPException(409, "Only jobs with no candidates or rejected/archived candidates can be deleted")
        db.delete(job)
        return {"status": "deleted", "job_id": job_id, "removed_applications": len(linked)}


@app.post("/api/applications", status_code=201)
async def create_application(payload: ApplicationCreate, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        item = await build_application(db, owner_id, require_job(db, payload.job_id, owner_id), payload.candidate_name,
                                       payload.candidate_email, payload.resume_text)
        return application_dict(item)


@app.get("/api/applications/{application_id}/interview-kit")
async def get_interview_kit(application_id: str, owner_id: str = Depends(current_user_id)) -> dict:
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
async def create_batch(job_id: str = Form(...), files: list[UploadFile] = File(...), owner_id: str = Depends(current_user_id)) -> dict:
    if not 1 <= len(files) <= 20:
        raise HTTPException(422, "Mỗi batch phải có từ 1 đến 20 CV")
    task_ids: list[str] = []
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
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
def get_batch(batch_id: str, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.owner_id == owner_id))
        if not batch:
            raise HTTPException(404, "Batch not found")
        return batch_dict(db, batch)


@app.post("/api/application-batches/{batch_id}/items/{item_id}/retry", status_code=202)
async def retry_batch_item(batch_id: str, item_id: str, owner_id: str = Depends(current_user_id)) -> dict:
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
def get_agent_run(run_id: str, owner_id: str = Depends(current_user_id)) -> dict:
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
            "error": run.error,
            "steps": [{"node": step.node, "status": step.status, "attempt": step.attempt,
                       "latency_ms": step.latency_ms, "error": step.error, "metadata": step.metadata_json}
                      for step in steps],
        }


@app.post("/api/applications/{application_id}/review")
def review(application_id: str, payload: ReviewCreate, owner_id: str = Depends(current_user_id)) -> dict:
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
def available_slots(interviewer_id: str, owner_id: str = Depends(current_user_id)) -> list[dict]:
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    candidates = [base + timedelta(days=d, hours=h) for d, h in ((0, 2), (0, 7), (1, 3), (2, 2))]
    with session_scope() as db:
        booked = {value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
                  for value in db.scalars(select(Interview.start_at).where(Interview.owner_id == owner_id))}
    return [{"start_at": slot.isoformat(), "duration_minutes": 60} for slot in candidates if slot.isoformat() not in booked]


@app.post("/api/applications/{application_id}/interview", status_code=201)
def book_interview(application_id: str, payload: BookingCreate, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
        if db.scalar(select(Interview).where(Interview.owner_id == owner_id, Interview.start_at == payload.slot)):
            raise HTTPException(409, "Slot is no longer available")
        interview_id = str(uuid4())
        value = Interview(id=interview_id, owner_id=owner_id, application_id=item.id, start_at=payload.slot,
                          end_at=payload.slot + timedelta(hours=1), status=InterviewStatus.SCHEDULED.value,
                          meeting_url=f"https://meet.example/{interview_id[:8]}")
        db.add(value); item.status = ApplicationStatus.INTERVIEW_SCHEDULED.value
        audit(db, owner_id, item.id, "INTERVIEW_SCHEDULED", {"interview_id": interview_id})
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "Slot is no longer available") from None
        return interview_dict(value)


@app.get("/api/audit-logs")
def logs(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=80),
    application_id: str | None = None,
    owner_id: str = Depends(current_user_id),
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
                  requirements={**extract_requirements(SAMPLE_JD), "approval": {"status": "APPROVED", "note": "Seed data"}})
        db.add(job); db.flush()
        screening = screen_candidate(SAMPLE_CV, job.requirements)
        screening["interview_kit"] = generate_interview_kit(SAMPLE_CV, job.requirements, screening, job.title)
        db.add(Application(id="app-001", owner_id=owner_id, job_id=job.id, candidate_name="Nguyễn Minh Anh",
                           candidate_email="minhanh@example.com", resume_text=SAMPLE_CV,
                           screening=screening, pipeline=pipeline()))
