from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .statuses import ApplicationStatus, BatchStatus, InterviewStatus, JobStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    __table_args__ = (UniqueConstraint("owner_id", "start_at", name="uq_interviews_owner_start"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default=InterviewStatus.SCHEDULED.value)
    meeting_url: Mapped[str] = mapped_column(String(500))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
