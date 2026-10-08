from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CvDocument(Base):
    __tablename__ = "cv_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "checksum", name="uq_cv_tenant_checksum"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="Ứng viên chưa xác định")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    location: Mapped[str] = mapped_column(String(200), default="", index=True)
    job_title: Mapped[str] = mapped_column(String(200), default="")
    specialization: Mapped[str] = mapped_column(String(40), default="OTHER", index=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    experience_years: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    education_level: Mapped[str] = mapped_column(String(120), default="")
    languages: Mapped[list] = mapped_column(JSON, default=list)
    level: Mapped[str] = mapped_column(String(40), default="")
    certifications: Mapped[list] = mapped_column(JSON, default=list)
    # Derived fields a person corrected by hand; re-extraction leaves them alone.
    edited_fields: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(120), default="UPLOAD", index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(120))
    file_size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="READY", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CvChunk(Base):
    __tablename__ = "cv_chunks"
    __table_args__ = (UniqueConstraint("cv_id", "ordinal", name="uq_cv_chunk_ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    cv_id: Mapped[str] = mapped_column(ForeignKey("cv_documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    section_type: Mapped[str] = mapped_column(String(80), default="CONTENT")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(24), default="PENDING")


class ServiceApiKey(Base):
    __tablename__ = "service_api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(String(320), default="")
    action: Mapped[str] = mapped_column(String(100), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
