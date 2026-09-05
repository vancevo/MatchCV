"""Add provider integrations, self-scheduling and transactional outbox.

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("account_email", sa.String(320), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "provider", name="uq_integration_owner_provider"),
    )
    for column in ("owner_id", "provider", "status"):
        op.create_index(f"ix_integration_connections_{column}", "integration_connections", [column])

    op.create_table(
        "scheduling_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("timezone_name", sa.String(80), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_scheduling_invitation_token_hash"),
    )
    for column in ("owner_id", "application_id", "status", "expires_at"):
        op.create_index(f"ix_scheduling_invitations_{column}", "scheduling_invitations", [column])

    op.create_table(
        "email_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "key", "version", name="uq_email_template_version"),
    )
    op.create_index("ix_email_templates_owner_id", "email_templates", ["owner_id"])
    op.create_index("ix_email_templates_key", "email_templates", ["key"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("last_error", sa.Text()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
    )
    for column in ("owner_id", "aggregate_id", "operation", "status"):
        op.create_index(f"ix_outbox_events_{column}", "outbox_events", [column])

    op.create_table(
        "provider_webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_provider_webhook_external"),
    )
    op.create_index("ix_provider_webhook_events_provider", "provider_webhook_events", ["provider"])
    op.create_index("ix_provider_webhook_events_event_type", "provider_webhook_events", ["event_type"])

    with op.batch_alter_table("interviews") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(32), nullable=False, server_default="local"))
        batch_op.add_column(sa.Column("external_event_id", sa.String(255)))
        batch_op.add_column(sa.Column("timezone_name", sa.String(80), nullable=False, server_default="UTC"))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(255)))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True)))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM interviews")).fetchall()
    for row in rows:
        connection.execute(
            sa.text("UPDATE interviews SET idempotency_key=:key, updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"key": f"legacy-interview:{row[0]}", "id": row[0]},
        )
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.alter_column("idempotency_key", existing_type=sa.String(255), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_unique_constraint("uq_interviews_idempotency_key", ["idempotency_key"])
        batch_op.create_index("ix_interviews_external_event_id", ["external_event_id"])


def downgrade() -> None:
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.drop_index("ix_interviews_external_event_id")
        batch_op.drop_constraint("uq_interviews_idempotency_key", type_="unique")
        for column in ("updated_at", "idempotency_key", "timezone_name", "external_event_id", "provider"):
            batch_op.drop_column(column)
    for table in ("provider_webhook_events", "outbox_events", "email_templates", "scheduling_invitations", "integration_connections"):
        op.drop_table(table)
