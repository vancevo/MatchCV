"""Dialect-aware top-K retrieval for candidate search chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import bindparam, select, text

from .candidate_search import cosine_similarity
from .candidate_search_document import CHUNK_TEMPLATE_VERSION
from .embedding_service import EmbeddingRuntimeConfig
from .models import CandidateSearchChunk


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    candidate_profile_id: str
    resume_version_id: str
    section_type: str
    ordinal: int
    text: str
    dense_similarity: float | None
    lexical_score: float


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"\w+", value.casefold(), flags=re.UNICODE) if len(token) > 1}


def _lexical_overlap(query_tokens: set[str], document: str) -> float:
    return len(query_tokens & _tokens(document)) / max(1, len(query_tokens))


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.9g}" for value in vector) + "]"


def _postgres_chunk_hits(
    db,
    *,
    owner_id: str,
    version_ids: Sequence[str],
    query: str,
    query_vector: Sequence[float] | None,
    config: EmbeddingRuntimeConfig,
    limit: int,
) -> list[ChunkHit]:
    common = """
        owner_id = :owner_id
        AND resume_version_id IN :version_ids
        AND model_name = :model_name
        AND model_revision = :model_revision
        AND template_version = :template_version
        AND status = 'READY'
    """
    params = {
        "owner_id": owner_id,
        "version_ids": list(version_ids),
        "model_name": config.model_name,
        "model_revision": config.model_revision,
        "template_version": CHUNK_TEMPLATE_VERSION,
        "limit": limit,
    }
    merged: dict[str, dict] = {}
    if query_vector is not None:
        dense_sql = text(f"""
            SELECT id, candidate_profile_id, resume_version_id, section_type, ordinal, text,
                   1 - (embedding <=> CAST(:query_vector AS vector)) AS dense_similarity
            FROM candidate_search_chunks
            WHERE {common}
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :limit
        """).bindparams(bindparam("version_ids", expanding=True))
        for row in db.execute(dense_sql, {**params, "query_vector": _vector_literal(query_vector)}).mappings():
            merged[row["id"]] = dict(row) | {"lexical_score": 0.0}

    lexical_dense = (
        "1 - (embedding <=> CAST(:query_vector AS vector))"
        if query_vector is not None else "NULL"
    )
    query_tokens = sorted(_tokens(query))
    if not query_tokens:
        lexical_rows = []
    else:
        lexical_sql = text(f"""
        SELECT id, candidate_profile_id, resume_version_id, section_type, ordinal, text,
               {lexical_dense} AS dense_similarity,
               ts_rank_cd(to_tsvector('simple', text), to_tsquery('simple', :lexical_query)) AS lexical_score
        FROM candidate_search_chunks
        WHERE {common}
          AND to_tsvector('simple', text) @@ to_tsquery('simple', :lexical_query)
        ORDER BY lexical_score DESC
        LIMIT :limit
        """).bindparams(bindparam("version_ids", expanding=True))
        lexical_params = {**params, "lexical_query": " | ".join(query_tokens)}
        if query_vector is not None:
            lexical_params["query_vector"] = _vector_literal(query_vector)
        lexical_rows = list(db.execute(lexical_sql, lexical_params).mappings())
    maximum_lexical = max((float(row["lexical_score"] or 0) for row in lexical_rows), default=0.0)
    for row in lexical_rows:
        value = merged.setdefault(row["id"], dict(row) | {"dense_similarity": None})
        value["lexical_score"] = (
            float(row["lexical_score"] or 0) / maximum_lexical if maximum_lexical else 0.0
        )
    return [ChunkHit(
        chunk_id=value["id"],
        candidate_profile_id=value["candidate_profile_id"],
        resume_version_id=value["resume_version_id"],
        section_type=value["section_type"],
        ordinal=int(value["ordinal"]),
        text=value["text"],
        dense_similarity=(float(value["dense_similarity"]) if value.get("dense_similarity") is not None else None),
        lexical_score=float(value.get("lexical_score") or 0),
    ) for value in merged.values()]


def _portable_chunk_hits(
    db,
    *,
    owner_id: str,
    version_ids: Sequence[str],
    query: str,
    query_vector: Sequence[float] | None,
    config: EmbeddingRuntimeConfig,
    limit: int,
) -> list[ChunkHit]:
    chunks = list(db.scalars(select(CandidateSearchChunk).where(
        CandidateSearchChunk.owner_id == owner_id,
        CandidateSearchChunk.resume_version_id.in_(list(version_ids)),
        CandidateSearchChunk.model_name == config.model_name,
        CandidateSearchChunk.model_revision == config.model_revision,
        CandidateSearchChunk.template_version == CHUNK_TEMPLATE_VERSION,
        CandidateSearchChunk.status == "READY",
    )))
    query_tokens = _tokens(query)
    hits = [ChunkHit(
        chunk_id=chunk.id,
        candidate_profile_id=chunk.candidate_profile_id,
        resume_version_id=chunk.resume_version_id,
        section_type=chunk.section_type,
        ordinal=chunk.ordinal,
        text=chunk.text,
        dense_similarity=(cosine_similarity(query_vector, chunk.embedding)
                          if query_vector is not None and chunk.embedding else None),
        lexical_score=_lexical_overlap(query_tokens, chunk.text),
    ) for chunk in chunks]
    hits.sort(key=lambda item: (
        item.dense_similarity if item.dense_similarity is not None else -1,
        item.lexical_score,
    ), reverse=True)
    dense_ids = {item.chunk_id for item in hits[:limit]}
    lexical_ids = {
        item.chunk_id for item in sorted(hits, key=lambda value: value.lexical_score, reverse=True)[:limit]
        if item.lexical_score > 0
    }
    return [item for item in hits if item.chunk_id in dense_ids | lexical_ids]


def retrieve_candidate_chunks(
    db,
    *,
    owner_id: str,
    version_ids: Sequence[str],
    query: str,
    query_vector: Sequence[float] | None,
    config: EmbeddingRuntimeConfig,
    limit: int,
) -> list[ChunkHit]:
    """Retrieve an oversampled top-K union from dense and lexical channels."""
    if not version_ids or limit <= 0:
        return []
    if db.bind.dialect.name == "postgresql":
        return _postgres_chunk_hits(
            db, owner_id=owner_id, version_ids=version_ids, query=query,
            query_vector=query_vector, config=config, limit=limit,
        )
    return _portable_chunk_hits(
        db, owner_id=owner_id, version_ids=version_ids, query=query,
        query_vector=query_vector, config=config, limit=limit,
    )
