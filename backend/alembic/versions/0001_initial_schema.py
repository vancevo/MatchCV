"""Create the initial TalentFlow schema.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()
    if "jobs" not in existing:
        op.create_table(
            "jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("department", sa.String(120), nullable=False),
            sa.Column("location", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("requirements", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "upload_batches" not in existing:
        op.create_table(
            "upload_batches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False),
            sa.Column("completed", sa.Integer(), nullable=False),
            sa.Column("failed", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "applications" not in existing:
        op.create_table(
            "applications",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("batch_id", sa.String(36), sa.ForeignKey("upload_batches.id", ondelete="SET NULL")),
            sa.Column("candidate_name", sa.String(200), nullable=False),
            sa.Column("candidate_email", sa.String(320), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("resume_filename", sa.String(255)),
            sa.Column("resume_size", sa.Integer()),
            sa.Column("resume_checksum", sa.String(64)),
            sa.Column("resume_text", sa.Text(), nullable=False),
            sa.Column("screening", sa.JSON(), nullable=False),
            sa.Column("pipeline", sa.JSON(), nullable=False),
            sa.Column("review", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "interviews" not in existing:
        op.create_table(
            "interviews",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("meeting_url", sa.String(500), nullable=False),
            sa.UniqueConstraint("owner_id", "start_at", name="uq_interviews_owner_start"),
        )
    elif "uq_interviews_owner_start" not in {item["name"] for item in inspect(op.get_bind()).get_unique_constraints("interviews")}:
        with op.batch_alter_table("interviews") as batch_op:
            batch_op.create_unique_constraint("uq_interviews_owner_start", ["owner_id", "start_at"])
    if "audit_logs" not in existing:
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("application_id", sa.String(36)),
            sa.Column("action", sa.String(80), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    indexes = [
        ("ix_jobs_owner_id", "jobs", ["owner_id"]),
        ("ix_upload_batches_owner_id", "upload_batches", ["owner_id"]),
        ("ix_upload_batches_job_id", "upload_batches", ["job_id"]),
        ("ix_applications_owner_id", "applications", ["owner_id"]),
        ("ix_applications_job_id", "applications", ["job_id"]),
        ("ix_applications_batch_id", "applications", ["batch_id"]),
        ("ix_applications_resume_checksum", "applications", ["resume_checksum"]),
        ("ix_interviews_owner_id", "interviews", ["owner_id"]),
        ("ix_interviews_application_id", "interviews", ["application_id"]),
        ("ix_audit_logs_owner_id", "audit_logs", ["owner_id"]),
        ("ix_audit_logs_application_id", "audit_logs", ["application_id"]),
    ]
    inspector = inspect(op.get_bind())
    for name, table, columns in indexes:
        if name not in {item["name"] for item in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade() -> None:
    for table in ("audit_logs", "interviews", "applications", "upload_batches", "jobs"):
        op.drop_table(table)
