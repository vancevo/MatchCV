"""Make interview working hours configurable and store busy periods.

Revision ID: 0012
Revises: 0011
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_policies", sa.Column("timezone_name", sa.String(80), nullable=False,
                                                  server_default="Asia/Ho_Chi_Minh"))
    op.add_column("interview_policies", sa.Column("working_days", sa.JSON(), nullable=False,
                                                  server_default="[0, 1, 2, 3, 4]"))
    op.add_column("interview_policies", sa.Column("working_start_hour", sa.Integer(), nullable=False,
                                                  server_default="9"))
    op.add_column("interview_policies", sa.Column("working_end_hour", sa.Integer(), nullable=False,
                                                  server_default="17"))
    op.create_table(
        "busy_blocks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_busy_blocks_owner_id", "busy_blocks", ["owner_id"])
    op.create_index("ix_busy_blocks_start_at", "busy_blocks", ["start_at"])


def downgrade() -> None:
    op.drop_index("ix_busy_blocks_start_at", table_name="busy_blocks")
    op.drop_index("ix_busy_blocks_owner_id", table_name="busy_blocks")
    op.drop_table("busy_blocks")
    op.drop_column("interview_policies", "working_end_hour")
    op.drop_column("interview_policies", "working_start_hour")
    op.drop_column("interview_policies", "working_days")
    op.drop_column("interview_policies", "timezone_name")
