"""Add follow-up, scorecard and feedback operations.

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scheduling_invitations") as batch_op:
        batch_op.add_column(sa.Column("purpose", sa.String(32), nullable=False, server_default="SCHEDULE"))
        batch_op.create_index("ix_scheduling_invitations_purpose", ["purpose"])
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.add_column(sa.Column("reschedule_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("outcome", sa.String(32)))

    op.create_table(
        "interview_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("reminder_minutes", sa.JSON(), nullable=False),
        sa.Column("max_reschedules", sa.Integer(), nullable=False),
        sa.Column("feedback_due_hours", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", name="uq_interview_policies_owner"),
    )
    op.create_index("ix_interview_policies_owner_id", "interview_policies", ["owner_id"])

    op.create_table(
        "interview_scorecards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("interview_id", sa.String(36), sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interviewer_email", sa.String(320), nullable=False),
        sa.Column("rubric", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interview_id", "interviewer_email", name="uq_scorecard_interviewer"),
    )
    op.create_index("ix_interview_scorecards_owner_id", "interview_scorecards", ["owner_id"])
    op.create_index("ix_interview_scorecards_interview_id", "interview_scorecards", ["interview_id"])

    op.create_table(
        "feedback_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("interview_id", sa.String(36), sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("concerns", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interview_id", "version", name="uq_feedback_summary_version"),
    )
    op.create_index("ix_feedback_summaries_owner_id", "feedback_summaries", ["owner_id"])
    op.create_index("ix_feedback_summaries_interview_id", "feedback_summaries", ["interview_id"])


def downgrade() -> None:
    op.drop_table("feedback_summaries")
    op.drop_table("interview_scorecards")
    op.drop_table("interview_policies")
    with op.batch_alter_table("interviews") as batch_op:
        batch_op.drop_column("outcome")
        batch_op.drop_column("reschedule_count")
    with op.batch_alter_table("scheduling_invitations") as batch_op:
        batch_op.drop_index("ix_scheduling_invitations_purpose")
        batch_op.drop_column("purpose")
