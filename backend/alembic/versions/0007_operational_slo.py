"""Add Phase 7 operational SLO alerts and release gates.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_slo_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("min_screening_success_rate", sa.Float(), nullable=False),
        sa.Column("max_p95_latency_ms", sa.Integer(), nullable=False),
        sa.Column("max_due_outbox", sa.Integer(), nullable=False),
        sa.Column("max_failed_outbox", sa.Integer(), nullable=False),
        sa.Column("max_stale_approvals", sa.Integer(), nullable=False),
        sa.Column("budget_warning_percent", sa.Integer(), nullable=False),
        sa.Column("min_canary_samples", sa.Integer(), nullable=False),
        sa.Column("max_success_rate_drop", sa.Float(), nullable=False),
        sa.Column("max_latency_regression_percent", sa.Integer(), nullable=False),
        sa.Column("max_cost_regression_percent", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", name="uq_operational_slo_policy_owner"),
    )
    op.create_index("ix_operational_slo_policies_owner_id", "operational_slo_policies", ["owner_id"])
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("signal", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("observed", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.JSON(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "signal", name="uq_operational_alert_signal"),
    )
    op.create_index("ix_operational_alerts_owner_id", "operational_alerts", ["owner_id"])
    op.create_index("ix_operational_alerts_signal", "operational_alerts", ["signal"])
    op.create_index("ix_operational_alerts_severity", "operational_alerts", ["severity"])
    op.create_index("ix_operational_alerts_status", "operational_alerts", ["status"])
    op.create_table(
        "release_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("release_version", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("candidate", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "release_version", name="uq_release_gate_version"),
    )
    op.create_index("ix_release_gates_owner_id", "release_gates", ["owner_id"])
    op.create_index("ix_release_gates_release_version", "release_gates", ["release_version"])
    op.create_index("ix_release_gates_status", "release_gates", ["status"])


def downgrade() -> None:
    op.drop_table("release_gates")
    op.drop_table("operational_alerts")
    op.drop_table("operational_slo_policies")
