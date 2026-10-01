"""Store the invitation email's Message-ID/thread so the confirmation email can reply in-thread.

Revision ID: 0022
Revises: 0021
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scheduling_invitations", sa.Column("email_message_id", sa.String(255), nullable=True))
    op.add_column("scheduling_invitations", sa.Column("email_thread_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduling_invitations", "email_thread_id")
    op.drop_column("scheduling_invitations", "email_message_id")
