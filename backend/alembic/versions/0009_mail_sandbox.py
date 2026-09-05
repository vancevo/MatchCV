"""Add tenant mail sandbox whitelist.

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant_policies", sa.Column("mail_sandbox_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenant_policies", sa.Column("mail_sandbox_base_email", sa.String(320), nullable=False, server_default=""))
    op.add_column("tenant_policies", sa.Column("mail_sandbox_max_alias", sa.Integer(), nullable=False, server_default="100"))


def downgrade() -> None:
    op.drop_column("tenant_policies", "mail_sandbox_max_alias")
    op.drop_column("tenant_policies", "mail_sandbox_base_email")
    op.drop_column("tenant_policies", "mail_sandbox_enabled")
