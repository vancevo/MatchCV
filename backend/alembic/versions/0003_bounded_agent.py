"""Add bounded screening and shortlist agent state.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


class Vector96(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw) -> str:
        return "vector(96)"


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "criteria_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("criteria_versions.id", ondelete="SET NULL")),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "version", name="uq_criteria_versions_job_version"),
    )
    op.create_index("ix_criteria_versions_owner_id", "criteria_versions", ["owner_id"])
    op.create_index("ix_criteria_versions_job_id", "criteria_versions", ["job_id"])

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("criteria_version_id", sa.String(36), sa.ForeignKey("criteria_versions.id", name="fk_agent_runs_criteria_version", ondelete="SET NULL")))
        batch_op.add_column(sa.Column("parent_run_id", sa.String(36), sa.ForeignKey("agent_runs.id", name="fk_agent_runs_parent", ondelete="SET NULL")))
        batch_op.add_column(sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("cost_micros", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("trace", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.create_index("ix_agent_runs_criteria_version_id", ["criteria_version_id"])

    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.alter_column("batch_id", existing_type=sa.String(36), nullable=True)
        batch_op.alter_column("batch_item_id", existing_type=sa.String(36), nullable=True)

    embedding_type = sa.JSON()
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        embedding_type = Vector96()
    op.create_table(
        "screening_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("criteria_version_id", sa.String(36), sa.ForeignKey("criteria_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_screening_artifacts_run_id"),
    )
    for column in ("owner_id", "application_id", "run_id", "criteria_version_id"):
        op.create_index(f"ix_screening_artifacts_{column}", "screening_artifacts", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE INDEX ix_screening_artifacts_embedding_cosine ON screening_artifacts USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "shortlist_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("criteria_version_id", sa.String(36), sa.ForeignKey("criteria_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("application_ids", sa.JSON(), nullable=False),
        sa.Column("ranking", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_shortlist_proposals_idempotency_key"),
    )
    for column in ("owner_id", "job_id", "criteria_version_id"):
        op.create_index(f"ix_shortlist_proposals_{column}", "shortlist_proposals", [column])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("request_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE")),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_approval_requests_dedupe_key"),
    )
    for column in ("owner_id", "request_type", "status", "job_id", "application_id", "resource_id"):
        op.create_index(f"ix_approval_requests_{column}", "approval_requests", [column])


def downgrade() -> None:
    op.drop_table("approval_requests")
    op.drop_table("shortlist_proposals")
    op.drop_table("screening_artifacts")
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.alter_column("batch_item_id", existing_type=sa.String(36), nullable=False)
        batch_op.alter_column("batch_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_criteria_version_id")
        for column in ("trace", "cost_micros", "output_tokens", "input_tokens", "parent_run_id", "criteria_version_id"):
            batch_op.drop_column(column)
    op.drop_table("criteria_versions")
