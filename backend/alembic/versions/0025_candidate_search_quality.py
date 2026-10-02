"""Add chunk retrieval, feedback telemetry, and search calibration.

Revision ID: 0025
Revises: 0024
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


class Vector1024(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "vector(1024)"


revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    embedding_type = sa.JSON()
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        embedding_type = Vector1024()

    op.create_table(
        "candidate_search_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("candidate_profile_id", sa.String(36), sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_type", sa.String(80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("model_revision", sa.String(120), nullable=False, server_default=""),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("template_version", sa.String(80), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("resume_version_id", "section_type", "ordinal", "model_name", "model_revision", "template_version", name="uq_candidate_search_chunk_version"),
    )
    for column in ("owner_id", "candidate_profile_id", "resume_version_id", "section_type", "content_hash", "status"):
        op.create_index(f"ix_candidate_search_chunks_{column}", "candidate_search_chunks", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_candidate_search_chunks_cosine ON candidate_search_chunks USING hnsw (embedding vector_cosine_ops)")
        op.execute("CREATE INDEX ix_candidate_search_chunks_fts ON candidate_search_chunks USING gin (to_tsvector('simple', text))")

    op.create_table(
        "candidate_search_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(320), nullable=False, server_default=""),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("score_version", sa.String(80), nullable=False),
        sa.Column("embedding_model", sa.String(160), nullable=False, server_default=""),
        sa.Column("embedding_revision", sa.String(120), nullable=False, server_default=""),
        sa.Column("reranker_model", sa.String(160), nullable=False, server_default=""),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fallback_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("owner_id", "actor_id", "query_hash", "created_at"):
        op.create_index(f"ix_candidate_search_events_{column}", "candidate_search_events", [column])

    op.create_table(
        "candidate_search_result_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("search_event_id", sa.String(36), sa.ForeignKey("candidate_search_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_profile_id", sa.String(36), sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_chunk_id", sa.String(36), sa.ForeignKey("candidate_search_chunks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("ranking_score", sa.Float(), nullable=False),
        sa.Column("dense_cosine", sa.Float(), nullable=True),
        sa.Column("lexical_score", sa.Float(), nullable=True),
        sa.Column("reranker_score", sa.Float(), nullable=True),
        sa.Column("score_components", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("search_event_id", "rank", name="uq_candidate_search_result_rank"),
    )
    for column in ("owner_id", "search_event_id", "candidate_profile_id", "resume_version_id", "evidence_chunk_id", "created_at"):
        op.create_index(f"ix_candidate_search_result_events_{column}", "candidate_search_result_events", [column])

    op.create_table(
        "candidate_search_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("result_event_id", sa.String(36), sa.ForeignKey("candidate_search_result_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(320), nullable=False, server_default=""),
        sa.Column("relevance", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("result_event_id", "actor_id", name="uq_candidate_search_feedback_actor"),
    )
    for column in ("owner_id", "result_event_id", "actor_id"):
        op.create_index(f"ix_candidate_search_feedback_{column}", "candidate_search_feedback", [column])

    op.create_table(
        "candidate_search_calibrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("score_version", sa.String(80), nullable=False),
        sa.Column("method", sa.String(40), nullable=False, server_default="PLATT"),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("fitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "version", name="uq_candidate_search_calibration_version"),
    )
    op.create_index("ix_candidate_search_calibrations_owner_id", "candidate_search_calibrations", ["owner_id"])
    op.create_index("ix_candidate_search_calibrations_status", "candidate_search_calibrations", ["status"])


def downgrade() -> None:
    op.drop_table("candidate_search_calibrations")
    op.drop_table("candidate_search_feedback")
    op.drop_table("candidate_search_result_events")
    op.drop_table("candidate_search_events")
    op.drop_table("candidate_search_chunks")
