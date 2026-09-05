"""Add Phase 5 tenant governance, source ingestion and model policy.

Revision ID: 0006
Revises: 0005
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("tenants", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(200), nullable=False), sa.Column("created_by", sa.String(128), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_tenants_created_by", "tenants", ["created_by"])
    op.create_index("ix_tenants_status", "tenants", ["status"])
    op.create_table("tenant_memberships", sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.String(128), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"))
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_index("ix_tenant_memberships_status", "tenant_memberships", ["status"])
    op.create_table("tenant_policies", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("retention_days", sa.Integer(), nullable=False), sa.Column("monthly_screening_limit", sa.Integer(), nullable=False), sa.Column("screenings_per_minute", sa.Integer(), nullable=False), sa.Column("monthly_token_limit", sa.Integer(), nullable=False), sa.Column("monthly_cost_limit_micros", sa.Integer(), nullable=False), sa.Column("email_enabled", sa.Boolean(), nullable=False), sa.Column("calendar_enabled", sa.Boolean(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("owner_id", name="uq_tenant_policy_owner"))
    op.create_index("ix_tenant_policies_owner_id", "tenant_policies", ["owner_id"])
    op.create_table("tenant_usage", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("period", sa.String(7), nullable=False), sa.Column("screenings", sa.Integer(), nullable=False), sa.Column("input_tokens", sa.Integer(), nullable=False), sa.Column("output_tokens", sa.Integer(), nullable=False), sa.Column("cost_micros", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("owner_id", "period", name="uq_tenant_usage_period"))
    op.create_index("ix_tenant_usage_owner_id", "tenant_usage", ["owner_id"])
    op.create_index("ix_tenant_usage_period", "tenant_usage", ["period"])
    op.create_table("tenant_rate_windows", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("window_key", sa.String(16), nullable=False), sa.Column("request_count", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("owner_id", "window_key", name="uq_tenant_rate_window"))
    op.create_index("ix_tenant_rate_windows_owner_id", "tenant_rate_windows", ["owner_id"])
    op.create_index("ix_tenant_rate_windows_window_key", "tenant_rate_windows", ["window_key"])
    op.create_table("source_connectors", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("scopes", sa.JSON(), nullable=False), sa.Column("secret_hash", sa.String(64), nullable=False), sa.Column("consent_basis", sa.String(240), nullable=False), sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("owner_id", "name", name="uq_source_connector_name"))
    op.create_index("ix_source_connectors_owner_id", "source_connectors", ["owner_id"])
    op.create_index("ix_source_connectors_kind", "source_connectors", ["kind"])
    op.create_index("ix_source_connectors_status", "source_connectors", ["status"])
    op.create_table("source_ingestions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("connector_id", sa.String(36), sa.ForeignKey("source_connectors.id", ondelete="CASCADE"), nullable=False), sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="SET NULL")), sa.Column("source_ref", sa.String(255), nullable=False), sa.Column("source_uri", sa.String(1000), nullable=False), sa.Column("checksum", sa.String(64), nullable=False), sa.Column("candidate_consented_at", sa.DateTime(timezone=True), nullable=False), sa.Column("provenance", sa.JSON(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("connector_id", "source_ref", name="uq_source_ingestion_ref"))
    op.create_index("ix_source_ingestions_owner_id", "source_ingestions", ["owner_id"])
    op.create_index("ix_source_ingestions_connector_id", "source_ingestions", ["connector_id"])
    op.create_index("ix_source_ingestions_application_id", "source_ingestions", ["application_id"])
    op.create_index("ix_source_ingestions_checksum", "source_ingestions", ["checksum"])
    op.create_index("ix_source_ingestions_status", "source_ingestions", ["status"])
    op.create_table("model_policies", sa.Column("id", sa.String(36), primary_key=True), sa.Column("owner_id", sa.String(128), nullable=False), sa.Column("workflow", sa.String(80), nullable=False), sa.Column("champion", sa.JSON(), nullable=False), sa.Column("challenger", sa.JSON(), nullable=False), sa.Column("challenger_percent", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("evaluation_metrics", sa.JSON(), nullable=False), sa.Column("evaluated_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("owner_id", "workflow", name="uq_model_policy_workflow"))
    op.create_index("ix_model_policies_owner_id", "model_policies", ["owner_id"])
    op.create_index("ix_model_policies_status", "model_policies", ["status"])


def downgrade() -> None:
    for table in ["model_policies", "source_ingestions", "source_connectors", "tenant_rate_windows", "tenant_usage", "tenant_policies", "tenant_memberships", "tenants"]:
        op.drop_table(table)
