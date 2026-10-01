"""Each model's own overall score/recommendation/reasoning for the candidate in this interview.

Revision ID: 0024
Revises: 0023
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_cross_analysis_runs", sa.Column("score", sa.Integer(), nullable=True))
    op.add_column("interview_cross_analysis_runs", sa.Column("recommendation", sa.String(32), nullable=True))
    op.add_column("interview_cross_analysis_runs", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_cross_analysis_runs", "summary")
    op.drop_column("interview_cross_analysis_runs", "recommendation")
    op.drop_column("interview_cross_analysis_runs", "score")
