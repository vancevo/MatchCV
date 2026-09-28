"""Persistence bridge between immutable CV versions and the local embedding runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from .candidate_search_document import build_candidate_search_document
from .embedding_service import EmbeddingService, EmbeddingUnavailable, get_embedding_service
from .models import CandidateResumeVersion, ResumeVersionEmbedding, ResumeVersionSkill
from .resume_comparison import sync_resume_version_skills


def _entry_for(db, version: CandidateResumeVersion, service: EmbeddingService) -> ResumeVersionEmbedding | None:
    config = service.config
    return db.scalar(select(ResumeVersionEmbedding).where(
        ResumeVersionEmbedding.resume_version_id == version.id,
        ResumeVersionEmbedding.model_name == config.model_name,
        ResumeVersionEmbedding.model_revision == config.model_revision,
        ResumeVersionEmbedding.template_version == "candidate-search.v1",
    ))


def _document_for(db, version: CandidateResumeVersion):
    skills = list(db.scalars(select(ResumeVersionSkill).where(
        ResumeVersionSkill.resume_version_id == version.id,
    )))
    if not skills:
        sync_resume_version_skills(db, version)
        skills = list(db.scalars(select(ResumeVersionSkill).where(
            ResumeVersionSkill.resume_version_id == version.id,
        )))
    extraction = version.extraction if isinstance(version.extraction, dict) else {}
    candidate = extraction.get("candidate") if isinstance(extraction.get("candidate"), dict) else {}
    return build_candidate_search_document(
        extraction,
        canonical_skills=[item.canonical_name for item in skills],
        pii_values=[str(candidate.get(key) or "") for key in ("name", "email", "phone")],
    )


def index_resume_versions(
    db,
    versions: list[CandidateResumeVersion],
    *,
    service: EmbeddingService | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Create/update search documents and embed them in one batch when the model is available."""
    runtime = service or get_embedding_service()
    pending: list[tuple[ResumeVersionEmbedding, str]] = []
    indexed = 0
    skipped = 0
    for version in versions:
        if not version.extraction:
            skipped += 1
            continue
        document = _document_for(db, version)
        entry = _entry_for(db, version, runtime)
        if not entry:
            entry = ResumeVersionEmbedding(
                id=str(uuid4()), owner_id=version.owner_id,
                candidate_profile_id=version.candidate_profile_id,
                resume_version_id=version.id,
                search_document=document.text,
                canonical_skills=list(document.canonical_skills),
                experience_years=document.experience_years,
                model_name=runtime.config.model_name,
                model_revision=runtime.config.model_revision,
                embedding_dimension=runtime.config.dimension,
                template_version=document.template_version,
                content_hash=document.content_hash,
                status="PENDING",
            )
            db.add(entry)
        changed = entry.content_hash != document.content_hash
        entry.search_document = document.text
        entry.canonical_skills = list(document.canonical_skills)
        entry.experience_years = document.experience_years
        entry.content_hash = document.content_hash
        if not force and not changed and entry.status == "READY" and entry.embedding:
            skipped += 1
            continue
        entry.status = "PENDING"
        entry.error = ""
        pending.append((entry, document.text))
    db.flush()

    if not pending:
        return {"indexed": indexed, "skipped": skipped, "failed": 0, "status": runtime.status()}
    try:
        vectors = runtime.embed_documents([document for _, document in pending])
        now = datetime.now(timezone.utc)
        for (entry, _), vector in zip(pending, vectors):
            entry.embedding = vector
            entry.status = "READY"
            entry.error = ""
            entry.embedded_at = now
            indexed += 1
        return {"indexed": indexed, "skipped": skipped, "failed": 0, "status": runtime.status()}
    except EmbeddingUnavailable as exc:
        for entry, _ in pending:
            entry.embedding = []
            entry.status = "UNAVAILABLE"
            entry.error = str(exc)[:1000]
        return {"indexed": 0, "skipped": skipped, "failed": len(pending),
                "warning": str(exc), "status": runtime.status()}
    except Exception as exc:
        for entry, _ in pending:
            entry.embedding = []
            entry.status = "FAILED"
            entry.error = str(exc)[:1000]
        return {"indexed": 0, "skipped": skipped, "failed": len(pending),
                "warning": str(exc), "status": runtime.status()}


def index_resume_version(db, version: CandidateResumeVersion) -> dict[str, object]:
    return index_resume_versions(db, [version])
