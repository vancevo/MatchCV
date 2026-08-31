from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .auth import current_user_id
from .database import Base, engine, session_scope
from .llm import extract_requirements_ai, llm_config, screen_candidate_ai
from .models import Application, AuditLog, Interview, Job, UploadBatch
from .pipeline import extract_requirements, screen_candidate
from .resume import candidate_identity, checksum, extract_resume

app = FastAPI(title="TalentFlow AI API", version="0.2.0")
origins = [v.strip() for v in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
Base.metadata.create_all(bind=engine)

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


class BookingCreate(BaseModel):
    slot: datetime


def pipeline(review_status: str = "waiting") -> list[dict]:
    nodes = ["CV Uploaded", "Document Parsed", "Candidate Extracted", "Rule Matching", "Semantic Matching", "Evidence Generated"]
    return [{"node": n, "status": "completed"} for n in nodes] + [{"node": "Recruiter Review", "status": review_status}]


def audit(db, owner_id: str, application_id: str | None, action: str, metadata: dict | None = None) -> None:
    db.add(AuditLog(owner_id=owner_id, application_id=application_id, action=action, metadata_json=metadata or {}))


def job_dict(job: Job, count: int = 0) -> dict:
    return {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
            "description": job.description, "requirements": job.requirements, "status": job.status, "applications_count": count}


def application_dict(item: Application) -> dict:
    return {"id": item.id, "job_id": item.job_id, "batch_id": item.batch_id,
            "candidate": {"name": item.candidate_name, "email": item.candidate_email}, "status": item.status,
            "resume_filename": item.resume_filename, "resume_size": item.resume_size,
            "screening": item.screening, "pipeline": item.pipeline, "review": item.review}


def interview_dict(item: Interview) -> dict:
    return {"id": item.id, "application_id": item.application_id, "start_at": item.start_at.isoformat(),
            "end_at": item.end_at.isoformat(), "status": item.status, "meeting_url": item.meeting_url}


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
    item = Application(owner_id=owner_id, job_id=job.id, batch_id=batch_id, candidate_name=name,
                       candidate_email=email, resume_filename=filename, resume_size=size,
                       resume_checksum=digest, resume_text=text, screening=result, pipeline=pipeline())
    db.add(item); db.flush()
    audit(db, owner_id, item.id, "SCREENING_COMPLETED", {"score": result["final_score"], "pipeline_version": "v2"})
    return item


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "talentflow-api", "ai": llm_config(),
            "auth_required": os.getenv("AUTH_REQUIRED", "false").lower() in {"1", "true", "yes"}}


@app.get("/api/dashboard")
def dashboard(owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        jobs = list(db.scalars(select(Job).where(Job.owner_id == owner_id).order_by(Job.created_at.desc())))
        apps = list(db.scalars(select(Application).where(Application.owner_id == owner_id).order_by(Application.created_at.desc())))
        interviews = list(db.scalars(select(Interview).where(Interview.owner_id == owner_id).order_by(Interview.start_at)))
        counts = dict(db.execute(select(Application.job_id, func.count()).where(Application.owner_id == owner_id).group_by(Application.job_id)).all())
        ranked = sorted(apps, key=lambda a: a.screening.get("final_score", 0), reverse=True)
        return {"metrics": {"open_jobs": len(jobs), "candidates": len(apps),
                            "awaiting_review": sum(a.status == "WAITING_REVIEW" for a in apps), "interviews": len(interviews)},
                "applications": [application_dict(a) for a in ranked], "jobs": [job_dict(j, counts.get(j.id, 0)) for j in jobs],
                "interviews": [interview_dict(i) for i in interviews]}


@app.get("/api/jobs")
def list_jobs(owner_id: str = Depends(current_user_id)) -> list[dict]:
    with session_scope() as db:
        return [job_dict(j) for j in db.scalars(select(Job).where(Job.owner_id == owner_id))]


@app.post("/api/jobs", status_code=201)
async def create_job(payload: JobCreate, owner_id: str = Depends(current_user_id)) -> dict:
    requirements = await extract_requirements_ai(payload.description)
    with session_scope() as db:
        job = Job(owner_id=owner_id, **payload.model_dump(), requirements=requirements)
        db.add(job); db.flush()
        return job_dict(job)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        linked = list(db.scalars(select(Application).where(Application.job_id == job_id, Application.owner_id == owner_id)))
        if any(a.status not in {"REJECTED", "ARCHIVED"} for a in linked):
            raise HTTPException(409, "Only jobs with no candidates or rejected/archived candidates can be deleted")
        db.delete(job)
        return {"status": "deleted", "job_id": job_id, "removed_applications": len(linked)}


@app.post("/api/applications", status_code=201)
async def create_application(payload: ApplicationCreate, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        item = await build_application(db, owner_id, require_job(db, payload.job_id, owner_id), payload.candidate_name,
                                       payload.candidate_email, payload.resume_text)
        return application_dict(item)


@app.post("/api/application-batches", status_code=201)
async def create_batch(job_id: str = Form(...), files: list[UploadFile] = File(...), owner_id: str = Depends(current_user_id)) -> dict:
    if not 1 <= len(files) <= 20:
        raise HTTPException(422, "Mỗi batch phải có từ 1 đến 20 CV")
    with session_scope() as db:
        job = require_job(db, job_id, owner_id)
        batch = UploadBatch(owner_id=owner_id, job_id=job_id, total=len(files)); db.add(batch); db.flush()
        results: list[dict] = []
        for file in files:
            try:
                content, text = await extract_resume(file)
                name, email = candidate_identity(text, file.filename)
                item = await build_application(db, owner_id, job, name, email, text, file.filename, len(content), checksum(content), batch.id)
                batch.completed += 1
                audit(db, owner_id, item.id, "CV_EXTRACTED", {"filename": file.filename, "size": len(content), "stored_original": False})
                results.append({"filename": file.filename, "status": "COMPLETED", "application": application_dict(item)})
            except HTTPException as exc:
                batch.failed += 1; results.append({"filename": file.filename, "status": "FAILED", "error": exc.detail})
            except Exception:
                batch.failed += 1; results.append({"filename": file.filename, "status": "FAILED", "error": "Không thể xử lý CV"})
        batch.status = "COMPLETED" if not batch.failed else "PARTIAL" if batch.completed else "FAILED"
        return {"batch_id": batch.id, "status": batch.status, "total": batch.total, "completed": batch.completed,
                "failed": batch.failed, "items": results}


@app.get("/api/application-batches/{batch_id}")
def get_batch(batch_id: str, owner_id: str = Depends(current_user_id)) -> dict:
    with session_scope() as db:
        batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.owner_id == owner_id))
        if not batch:
            raise HTTPException(404, "Batch not found")
        apps = list(db.scalars(select(Application).where(Application.batch_id == batch_id, Application.owner_id == owner_id)))
        return {"batch_id": batch.id, "status": batch.status, "total": batch.total, "completed": batch.completed,
                "failed": batch.failed, "items": [{"filename": a.resume_filename, "status": "COMPLETED", "application": application_dict(a)} for a in apps]}


@app.post("/api/applications/{application_id}/review")
def review(application_id: str, payload: ReviewCreate, owner_id: str = Depends(current_user_id)) -> dict:
    statuses = {"INTERVIEW": "INTERVIEW_PENDING", "MANUAL_REVIEW": "REVIEWED", "REJECT": "REJECTED", "ARCHIVE": "ARCHIVED"}
    decision = payload.decision.upper()
    if decision not in statuses:
        raise HTTPException(422, "Unsupported decision")
    with session_scope() as db:
        item = require_application(db, application_id, owner_id)
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
                          end_at=payload.slot + timedelta(hours=1), status="SCHEDULED",
                          meeting_url=f"https://meet.example/{interview_id[:8]}")
        db.add(value); item.status = "INTERVIEW_SCHEDULED"
        audit(db, owner_id, item.id, "INTERVIEW_SCHEDULED", {"interview_id": interview_id})
        return interview_dict(value)


@app.get("/api/audit-logs")
def logs(owner_id: str = Depends(current_user_id)) -> list[dict]:
    with session_scope() as db:
        values = db.scalars(select(AuditLog).where(AuditLog.owner_id == owner_id).order_by(AuditLog.created_at.desc()))
        return [{"id": v.id, "application_id": v.application_id, "action": v.action,
                 "metadata": v.metadata_json, "created_at": v.created_at.isoformat()} for v in values]


def seed(owner_id: str = "00000000-0000-0000-0000-000000000001") -> None:
    with session_scope() as db:
        if db.scalar(select(Job).where(Job.owner_id == owner_id)):
            return
        job = Job(id="job-backend-01", owner_id=owner_id, title="Backend Python Developer", department="Engineering",
                  location="Hồ Chí Minh · Hybrid", description=SAMPLE_JD, requirements=extract_requirements(SAMPLE_JD))
        db.add(job); db.flush()
        db.add(Application(id="app-001", owner_id=owner_id, job_id=job.id, candidate_name="Nguyễn Minh Anh",
                           candidate_email="minhanh@example.com", resume_text=SAMPLE_CV,
                           screening=screen_candidate(SAMPLE_CV, job.requirements), pipeline=pipeline()))


if os.getenv("AUTO_SEED", "true").lower() in {"1", "true", "yes"}:
    seed()
