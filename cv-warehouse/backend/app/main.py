from __future__ import annotations

import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from .auth import principal, require_admin, require_scope
from .config import get_settings
from .database import Base, engine, session_scope
from .extraction import CONTENT_TYPES, chunks, extract_metadata, extract_text
from .models import AuditEvent, CvChunk, CvDocument, ServiceApiKey
from .search import cosine, embed, lexical_score, reranker_model


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Kho CV IT API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


class SearchFilters(BaseModel):
    required_skills: list[str] = Field(default_factory=list, max_length=30)
    preferred_skills: list[str] = Field(default_factory=list, max_length=30)
    minimum_experience: float | None = Field(default=None, ge=0, le=80)
    maximum_experience: float | None = Field(default=None, ge=0, le=80)
    specialization: str | None = None
    location: str | None = None
    source: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=5000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=20, ge=1, le=100)


class CvPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    specialization: str | None = Field(default=None, max_length=40)
    skills: list[str] | None = Field(default=None, max_length=100)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    education_level: str | None = Field(default=None, max_length=120)
    languages: list[str] | None = Field(default=None, max_length=30)
    source: str | None = Field(default=None, max_length=120)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["cvs.search", "cvs.read", "cvs.download"])


def cv_dict(value: CvDocument, *, include_text: bool = False) -> dict:
    result = {
        "id": value.id, "full_name": value.full_name, "email": value.email, "phone": value.phone,
        "location": value.location, "job_title": value.job_title, "specialization": value.specialization,
        "skills": value.skills or [], "experience_years": value.experience_years,
        "education_level": value.education_level, "languages": value.languages or [], "source": value.source,
        "original_filename": value.original_filename, "content_type": value.content_type,
        "file_size": value.file_size, "checksum": value.checksum, "status": value.status,
        "created_at": value.created_at, "updated_at": value.updated_at,
    }
    if include_text:
        result["extracted_text"] = value.extracted_text
    return result


def audit(db, who: dict[str, str], action: str, subject_id: str = "", detail: dict | None = None) -> None:
    db.add(AuditEvent(tenant_id=who["tenant_id"], actor_id=who["actor_id"], action=action,
                      subject_id=subject_id, detail=detail or {}))


async def save_upload(db, upload: UploadFile, who: dict[str, str], source: str, location: str) -> CvDocument:
    data = await upload.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"CV vượt quá {settings.max_upload_mb} MB")
    content_type = upload.content_type or "application/octet-stream"
    try:
        text = extract_text(data, content_type, upload.filename or "cv")
    except ValueError as exc:
        raise HTTPException(415, str(exc)) from exc
    if len(text) < 20:
        raise HTTPException(422, "Không trích xuất được đủ nội dung CV")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(CvDocument).where(
        CvDocument.tenant_id == who["tenant_id"], CvDocument.checksum == digest,
    ))
    if existing:
        return existing
    metadata = extract_metadata(text, upload.filename or "cv")
    cv_id = str(uuid4())
    suffix = CONTENT_TYPES.get(content_type, Path(upload.filename or "").suffix.lower())
    storage_key = f"{who['tenant_id']}/{cv_id}{suffix}"
    target = Path(settings.storage_dir) / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    value = CvDocument(
        id=cv_id, tenant_id=who["tenant_id"], source=source or "UPLOAD", location=location,
        original_filename=upload.filename or f"{cv_id}{suffix}", storage_key=storage_key,
        content_type=content_type, file_size=len(data), checksum=digest, extracted_text=text, **metadata,
    )
    db.add(value); db.flush()
    pieces = chunks(text)
    vectors = embed(pieces)
    for ordinal, (piece, vector) in enumerate(zip(pieces, vectors)):
        db.add(CvChunk(
            tenant_id=who["tenant_id"], cv_id=value.id, ordinal=ordinal, text=piece,
            embedding=vector, embedding_model=settings.embedding_model if vector else "",
            status="READY" if vector else "KEYWORD_ONLY",
        ))
    audit(db, who, "CV_UPLOADED", value.id, {"filename": value.original_filename, "checksum": digest})
    return value


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "service": "cv-warehouse"}


@app.get("/api/v1/ready")
def ready() -> dict:
    return {"status": "ready", "semantic_search": settings.embedding_enabled,
            "embedding_model": settings.embedding_model, "reranker": settings.reranker_enabled}


@app.post("/api/v1/cvs", status_code=201)
async def upload_cv(
    file: UploadFile = File(...), source: str = Form("UPLOAD"), location: str = Form(""),
    who: dict[str, str] = Depends(principal),
) -> dict:
    require_admin(who)
    with session_scope() as db:
        value = await save_upload(db, file, who, source, location)
        return cv_dict(value)


@app.post("/api/v1/cvs/bulk-upload", status_code=201)
async def bulk_upload(
    files: list[UploadFile] = File(...), source: str = Form("UPLOAD"), location: str = Form(""),
    who: dict[str, str] = Depends(principal),
) -> dict:
    require_admin(who)
    if len(files) > 20:
        raise HTTPException(422, "Mỗi batch tối đa 20 CV")
    results = []
    for upload in files:
        try:
            with session_scope() as db:
                value = await save_upload(db, upload, who, source, location)
                results.append({"filename": upload.filename, "status": "READY", "cv": cv_dict(value)})
        except HTTPException as exc:
            results.append({"filename": upload.filename, "status": "FAILED", "error": exc.detail})
    return {"items": results, "ready": sum(item["status"] == "READY" for item in results)}


@app.get("/api/v1/cvs")
def list_cvs(
    q: str | None = Query(default=None, max_length=200), limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0), who: dict[str, str] = Depends(principal),
) -> dict:
    require_scope(who, "cvs.read")
    with session_scope() as db:
        statement = select(CvDocument).where(CvDocument.tenant_id == who["tenant_id"], CvDocument.status != "ARCHIVED")
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            statement = statement.where(or_(CvDocument.full_name.ilike(pattern), CvDocument.email.ilike(pattern),
                                             CvDocument.job_title.ilike(pattern), CvDocument.extracted_text.ilike(pattern)))
        values = list(db.scalars(statement.order_by(CvDocument.updated_at.desc()).offset(offset).limit(limit)))
        return {"items": [cv_dict(value) for value in values], "limit": limit, "offset": offset}


@app.get("/api/v1/cvs/{cv_id}")
def get_cv(cv_id: str, who: dict[str, str] = Depends(principal)) -> dict:
    require_scope(who, "cvs.read")
    with session_scope() as db:
        value = db.scalar(select(CvDocument).where(CvDocument.id == cv_id, CvDocument.tenant_id == who["tenant_id"]))
        if not value:
            raise HTTPException(404, "Không tìm thấy CV")
        audit(db, who, "CV_READ", value.id)
        return cv_dict(value, include_text=True)


@app.patch("/api/v1/cvs/{cv_id}")
def update_cv(cv_id: str, payload: CvPatch, who: dict[str, str] = Depends(principal)) -> dict:
    require_admin(who)
    with session_scope() as db:
        value = db.scalar(select(CvDocument).where(CvDocument.id == cv_id, CvDocument.tenant_id == who["tenant_id"]))
        if not value:
            raise HTTPException(404, "Không tìm thấy CV")
        for key, field_value in payload.model_dump(exclude_unset=True).items():
            setattr(value, key, field_value)
        audit(db, who, "CV_UPDATED", value.id, {"fields": list(payload.model_fields_set)})
        return cv_dict(value)


@app.delete("/api/v1/cvs/{cv_id}")
def archive_cv(cv_id: str, who: dict[str, str] = Depends(principal)) -> dict:
    require_admin(who)
    with session_scope() as db:
        value = db.scalar(select(CvDocument).where(CvDocument.id == cv_id, CvDocument.tenant_id == who["tenant_id"]))
        if not value:
            raise HTTPException(404, "Không tìm thấy CV")
        value.status = "ARCHIVED"
        audit(db, who, "CV_ARCHIVED", value.id)
        return {"id": value.id, "status": value.status}


@app.get("/api/v1/cvs/{cv_id}/download")
def download_cv(cv_id: str, who: dict[str, str] = Depends(principal)):
    require_scope(who, "cvs.download")
    with session_scope() as db:
        value = db.scalar(select(CvDocument).where(CvDocument.id == cv_id, CvDocument.tenant_id == who["tenant_id"]))
        if not value:
            raise HTTPException(404, "Không tìm thấy CV")
        path = Path(settings.storage_dir) / value.storage_key
        if not path.exists():
            raise HTTPException(404, "File CV không còn trong storage")
        audit(db, who, "CV_DOWNLOADED", value.id)
        return FileResponse(path, media_type=value.content_type, filename=value.original_filename)


@app.post("/api/v1/cvs/search")
def search_cvs(payload: SearchRequest, who: dict[str, str] = Depends(principal)) -> dict:
    require_scope(who, "cvs.search")
    with session_scope() as db:
        values = list(db.scalars(select(CvDocument).where(
            CvDocument.tenant_id == who["tenant_id"], CvDocument.status == "READY",
        )))
        required = {value.casefold() for value in payload.filters.required_skills}
        filtered = []
        for value in values:
            skill_set = {skill.casefold() for skill in value.skills or []}
            if required and not required.issubset(skill_set):
                continue
            if payload.filters.minimum_experience is not None and value.experience_years < payload.filters.minimum_experience:
                continue
            if payload.filters.maximum_experience is not None and value.experience_years > payload.filters.maximum_experience:
                continue
            if payload.filters.specialization and value.specialization != payload.filters.specialization.upper():
                continue
            if payload.filters.location and payload.filters.location.casefold() not in value.location.casefold():
                continue
            if payload.filters.source and payload.filters.source.casefold() != value.source.casefold():
                continue
            filtered.append(value)
        query_vector = embed([payload.query])[0]
        rows = []
        for value in filtered:
            cv_chunks = list(db.scalars(select(CvChunk).where(CvChunk.cv_id == value.id).order_by(CvChunk.ordinal)))
            if query_vector and cv_chunks and any(not item.embedding for item in cv_chunks):
                indexed = embed([item.text for item in cv_chunks])
                for item, vector in zip(cv_chunks, indexed):
                    item.embedding = vector
                    item.embedding_model = settings.embedding_model if vector else ""
                    item.status = "READY" if vector else "KEYWORD_ONLY"
            best = max(cv_chunks, key=lambda item: (
                cosine(query_vector, item.embedding or []) if query_vector else lexical_score(payload.query, item.text)
            ), default=None)
            semantic = max(0.0, cosine(query_vector, best.embedding or [])) if query_vector and best else 0.0
            lexical = max((lexical_score(payload.query, item.text) for item in cv_chunks), default=0.0)
            preferred = {item.casefold() for item in payload.filters.preferred_skills}
            preferred_ratio = len(preferred & {skill.casefold() for skill in value.skills or []}) / len(preferred) if preferred else 0.0
            score = (semantic * 0.72 + lexical * 0.18 + preferred_ratio * 0.10) * 100
            if not query_vector:
                score = (lexical * 0.85 + preferred_ratio * 0.15) * 100
            rows.append({"cv": value, "score": score, "semantic": semantic, "lexical": lexical, "evidence": best.text[:600] if best else ""})
        rows.sort(key=lambda item: item["score"], reverse=True)
        reranker = reranker_model()
        if reranker and rows:
            head = rows[: min(30, len(rows))]
            reranked = reranker.predict([(payload.query, item["evidence"]) for item in head])
            for item, score in zip(head, reranked):
                item["reranker"] = float(score)
            head.sort(key=lambda item: item.get("reranker", 0.0), reverse=True)
            rows = head + rows[len(head):]
        rows = rows[:payload.limit]
        audit(db, who, "CV_SEARCHED", detail={"query_hash": hashlib.sha256(payload.query.casefold().encode()).hexdigest(),
                                               "result_count": len(rows)})
        return {
            "mode": "HYBRID_SEMANTIC" if query_vector else "KEYWORD_FALLBACK",
            "embedding_model": settings.embedding_model if query_vector else None,
            "reranker_model": settings.reranker_model if reranker else None,
            "results": [{**cv_dict(item["cv"]), "ranking_score": round(item["score"], 3),
                         "semantic_score": round(item["semantic"], 6) if query_vector else None,
                         "lexical_score": round(item["lexical"], 6),
                         "evidence": [{"section": "CV", "text": item["evidence"]}]} for item in rows],
        }


@app.get("/api/v1/filters")
def filters(who: dict[str, str] = Depends(principal)) -> dict:
    require_scope(who, "cvs.search")
    with session_scope() as db:
        values = list(db.scalars(select(CvDocument).where(
            CvDocument.tenant_id == who["tenant_id"], CvDocument.status == "READY",
        )))
    return {
        "skills": sorted({skill for value in values for skill in value.skills or []}),
        "locations": sorted({value.location for value in values if value.location}),
        "specializations": sorted({value.specialization for value in values if value.specialization}),
        "sources": sorted({value.source for value in values if value.source}),
    }


@app.post("/api/v1/api-keys", status_code=201)
def create_api_key(payload: ApiKeyCreate, who: dict[str, str] = Depends(principal)) -> dict:
    require_admin(who)
    allowed = {"cvs.search", "cvs.read", "cvs.download"}
    if not payload.scopes or set(payload.scopes) - allowed:
        raise HTTPException(422, "API key có scope không hợp lệ")
    token = f"cvw_{secrets.token_urlsafe(32)}"
    with session_scope() as db:
        value = ServiceApiKey(tenant_id=who["tenant_id"], name=payload.name,
                              key_hash=hashlib.sha256(token.encode()).hexdigest(), scopes=sorted(set(payload.scopes)))
        db.add(value); db.flush()
        audit(db, who, "API_KEY_CREATED", value.id, {"name": value.name, "scopes": value.scopes})
        return {"id": value.id, "name": value.name, "scopes": value.scopes, "token": token}
