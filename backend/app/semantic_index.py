"""Persistence bridge between immutable CV versions and the local embedding runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from .candidate_search_document import (
    CHUNK_TEMPLATE_VERSION,
    build_candidate_search_chunks,
    build_candidate_search_document,
)
from .embedding_service import EmbeddingService, EmbeddingUnavailable, get_embedding_service
from .models import CandidateResumeVersion, CandidateSearchChunk, ResumeVersionEmbedding, ResumeVersionSkill
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
    # pgvector rejects zero-dimensional values. PENDING/FAILED rows use a correctly
    # sized zero placeholder and are excluded from retrieval by their status.
    placeholder_vector = [0.0] * runtime.config.dimension
    pending: list[tuple[ResumeVersionEmbedding, str]] = []
    pending_chunks: list[tuple[CandidateSearchChunk, str]] = []
    indexed = 0
    chunks_indexed = 0
    skipped = 0
    chunks_skipped = 0
    for version in versions:
        if not version.extraction:
            skipped += 1
            continue
        document = _document_for(db, version)
        chunk_documents = build_candidate_search_chunks(document)
        entry = _entry_for(db, version, runtime)
        if not entry:
            entry = ResumeVersionEmbedding(
                id=str(uuid4()), owner_id=version.owner_id,
                candidate_profile_id=version.candidate_profile_id,
                resume_version_id=version.id,
                search_document=document.text,
                embedding=placeholder_vector,
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
        else:
            entry.status = "PENDING"
            entry.error = ""
            pending.append((entry, document.text))
        existing_chunks = list(db.scalars(select(CandidateSearchChunk).where(
            CandidateSearchChunk.resume_version_id == version.id,
            CandidateSearchChunk.model_name == runtime.config.model_name,
            CandidateSearchChunk.model_revision == runtime.config.model_revision,
            CandidateSearchChunk.template_version == CHUNK_TEMPLATE_VERSION,
        )))
        chunks_by_key = {(item.section_type, item.ordinal): item for item in existing_chunks}
        active_keys: set[tuple[str, int]] = set()
        for chunk_document in chunk_documents:
            key = (chunk_document.section_type, chunk_document.ordinal)
            active_keys.add(key)
            chunk = chunks_by_key.get(key)
            if chunk is None:
                chunk = CandidateSearchChunk(
                    id=str(uuid4()), owner_id=version.owner_id,
                    candidate_profile_id=version.candidate_profile_id,
                    resume_version_id=version.id,
                    section_type=chunk_document.section_type,
                    ordinal=chunk_document.ordinal,
                    text=chunk_document.text,
                    embedding=placeholder_vector,
                    model_name=runtime.config.model_name,
                    model_revision=runtime.config.model_revision,
                    embedding_dimension=runtime.config.dimension,
                    template_version=chunk_document.template_version,
                    content_hash=chunk_document.content_hash,
                    status="PENDING",
                )
                db.add(chunk)
            chunk_changed = chunk.content_hash != chunk_document.content_hash
            chunk.text = chunk_document.text
            chunk.content_hash = chunk_document.content_hash
            if not force and not chunk_changed and chunk.status == "READY" and chunk.embedding:
                chunks_skipped += 1
                continue
            chunk.status = "PENDING"
            chunk.error = ""
            pending_chunks.append((chunk, chunk_document.text))
        for key, stale in chunks_by_key.items():
            if key not in active_keys:
                db.delete(stale)
    db.flush()

    if not pending and not pending_chunks:
        return {"indexed": indexed, "skipped": skipped, "failed": 0,
                "chunks_indexed": 0, "chunks_skipped": chunks_skipped,
                "chunks_failed": 0, "status": runtime.status()}
    try:
        targets = [*pending, *pending_chunks]
        vectors = runtime.embed_documents([document for _, document in targets])
        now = datetime.now(timezone.utc)
        for (entry, _), vector in zip(targets, vectors):
            entry.embedding = vector
            entry.status = "READY"
            entry.error = ""
            entry.embedded_at = now
            if isinstance(entry, CandidateSearchChunk):
                chunks_indexed += 1
            else:
                indexed += 1
        return {"indexed": indexed, "skipped": skipped, "failed": 0,
                "chunks_indexed": chunks_indexed, "chunks_skipped": chunks_skipped,
                "chunks_failed": 0, "status": runtime.status()}
    except EmbeddingUnavailable as exc:
        for entry, _ in [*pending, *pending_chunks]:
            entry.embedding = placeholder_vector
            entry.status = "UNAVAILABLE"
            entry.error = str(exc)[:1000]
        return {"indexed": 0, "skipped": skipped, "failed": len(pending),
                "chunks_indexed": 0, "chunks_skipped": chunks_skipped,
                "chunks_failed": len(pending_chunks),
                "warning": str(exc), "status": runtime.status()}
    except Exception as exc:
        for entry, _ in [*pending, *pending_chunks]:
            entry.embedding = placeholder_vector
            entry.status = "FAILED"
            entry.error = str(exc)[:1000]
        return {"indexed": 0, "skipped": skipped, "failed": len(pending),
                "chunks_indexed": 0, "chunks_skipped": chunks_skipped,
                "chunks_failed": len(pending_chunks),
                "warning": str(exc), "status": runtime.status()}


def index_resume_version(db, version: CandidateResumeVersion) -> dict[str, object]:
    return index_resume_versions(db, [version])
