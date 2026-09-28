"""Add versioned semantic-search embeddings for CV snapshots.

Revision ID: 0018
Revises: 0017
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


class Vector1024(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "vector(1024)"


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    embedding_type = sa.JSON()
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        embedding_type = Vector1024()
    op.create_table(
        "resume_version_embeddings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("candidate_profile_id", sa.String(36), sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resume_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("search_document", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("canonical_skills", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("experience_years", sa.Float(), nullable=False, server_default="0"),
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
        sa.UniqueConstraint("resume_version_id", "model_name", "model_revision", "template_version", name="uq_resume_version_embedding_index"),
    )
    op.create_index("ix_resume_version_embeddings_owner_id", "resume_version_embeddings", ["owner_id"])
    op.create_index("ix_resume_version_embeddings_candidate_profile_id", "resume_version_embeddings", ["candidate_profile_id"])
    op.create_index("ix_resume_version_embeddings_resume_version_id", "resume_version_embeddings", ["resume_version_id"])
    op.create_index("ix_resume_version_embeddings_content_hash", "resume_version_embeddings", ["content_hash"])
    op.create_index("ix_resume_version_embeddings_status", "resume_version_embeddings", ["status"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_resume_version_embeddings_cosine "
            "ON resume_version_embeddings USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_table("resume_version_embeddings")
