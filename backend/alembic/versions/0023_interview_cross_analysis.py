"""Interview transcript cross-analysis: Q&A/claim/requirement links, multi-model runs.

Revision ID: 0023
Revises: 0022
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_transcript_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("interview_id", sa.String(36), sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interview_transcript_sessions_owner_id", "interview_transcript_sessions", ["owner_id"])
    op.create_index("ix_interview_transcript_sessions_interview_id", "interview_transcript_sessions", ["interview_id"])

    op.create_table(
        "interview_qa_pairs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("interview_transcript_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("speaker_role", sa.String(32), nullable=False, server_default=""),
        sa.Column("question_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_interview_qa_pairs_session_id", "interview_qa_pairs", ["session_id"])

    op.create_table(
        "interview_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("interview_transcript_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(320), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="skill"),
    )
    op.create_index("ix_interview_claims_session_id", "interview_claims", ["session_id"])

    op.create_table(
        "interview_cross_analysis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("interview_transcript_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OK"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "model_name", name="uq_cross_analysis_run_model"),
    )
    op.create_index("ix_interview_cross_analysis_runs_session_id", "interview_cross_analysis_runs", ["session_id"])

    op.create_table(
        "interview_qa_claim_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("qa_id", sa.String(36), sa.ForeignKey("interview_qa_pairs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("interview_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
    )
    op.create_index("ix_interview_qa_claim_links_qa_id", "interview_qa_claim_links", ["qa_id"])
    op.create_index("ix_interview_qa_claim_links_claim_id", "interview_qa_claim_links", ["claim_id"])
    op.create_index("ix_interview_qa_claim_links_model_name", "interview_qa_claim_links", ["model_name"])

    op.create_table(
        "interview_qa_requirement_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("qa_id", sa.String(36), sa.ForeignKey("interview_qa_pairs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_text", sa.String(320), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("evidence_strength", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_interview_qa_requirement_links_qa_id", "interview_qa_requirement_links", ["qa_id"])
    op.create_index("ix_interview_qa_requirement_links_model_name", "interview_qa_requirement_links", ["model_name"])


def downgrade() -> None:
    op.drop_table("interview_qa_requirement_links")
    op.drop_table("interview_qa_claim_links")
    op.drop_table("interview_cross_analysis_runs")
    op.drop_table("interview_claims")
    op.drop_table("interview_qa_pairs")
    op.drop_table("interview_transcript_sessions")
