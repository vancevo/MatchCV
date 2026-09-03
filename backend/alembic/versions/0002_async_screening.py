"""Add durable async screening state.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_batches") as batch_op:
        batch_op.add_column(sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="SET NULL")),
        sa.Column("task_id", sa.String(36)),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_items_owner_id", "batch_items", ["owner_id"])
    op.create_index("ix_batch_items_batch_id", "batch_items", ["batch_id"])
    op.create_index("ix_batch_items_application_id", "batch_items", ["application_id"])
    op.create_index("ix_batch_items_task_id", "batch_items", ["task_id"])
    op.create_index("ix_batch_items_checksum", "batch_items", ["checksum"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow", sa.String(80), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_node", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(200)),
        sa.Column("prompt_version", sa.String(120), nullable=False),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency_key"),
    )
    op.create_index("ix_agent_runs_owner_id", "agent_runs", ["owner_id"])
    op.create_index("ix_agent_runs_application_id", "agent_runs", ["application_id"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_steps_owner_id", "agent_steps", ["owner_id"])
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("queue_job_id", sa.String(255)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_agent_tasks_idempotency_key"),
    )
    op.create_index("ix_agent_tasks_owner_id", "agent_tasks", ["owner_id"])
    op.create_index("ix_agent_tasks_application_id", "agent_tasks", ["application_id"])
    op.create_index("ix_agent_tasks_batch_id", "agent_tasks", ["batch_id"])
    op.create_index("ix_agent_tasks_batch_item_id", "agent_tasks", ["batch_item_id"])
    op.create_index("ix_agent_tasks_run_id", "agent_tasks", ["run_id"])


def downgrade() -> None:
    op.drop_table("agent_tasks")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("batch_items")
    with op.batch_alter_table("upload_batches") as batch_op:
        batch_op.drop_column("skipped")
