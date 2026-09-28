"""Store the structured extraction snapshot on each CV version.

Revision ID: 0016
Revises: 0015
"""
from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _snapshot(application: dict) -> dict:
    screening = application.get("screening") if isinstance(application.get("screening"), dict) else {}
    structured = screening.get("candidate_profile")
    evidence = screening.get("evidence") if isinstance(screening.get("evidence"), list) else []
    if not isinstance(structured, dict):
        structured = {
            "skills": list(dict.fromkeys(
                str(item.get("requirement", "")).strip()
                for item in evidence if isinstance(item, dict) and item.get("matched")
                and str(item.get("requirement", "")).strip()
            )),
            "experience_years": screening.get("experience_years", 0),
            "education": [],
            "summary": "",
            "extraction_source": screening.get("screening_source", "rules"),
        }
    return {
        "candidate": {
            "name": application.get("candidate_name") or "",
            "email": application.get("candidate_email") or "",
            "phone": application.get("candidate_phone") or "",
        },
        "profile": structured,
        "evidence": evidence,
        "screening_source": screening.get("screening_source", structured.get("extraction_source", "rules")),
    }


def upgrade() -> None:
    with op.batch_alter_table("candidate_resume_versions") as batch_op:
        batch_op.add_column(sa.Column("extraction", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    applications = sa.table(
        "applications",
        sa.column("resume_version_id", sa.String()),
        sa.column("candidate_name", sa.String()),
        sa.column("candidate_email", sa.String()),
        sa.column("candidate_phone", sa.String()),
        sa.column("screening", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    versions = sa.table(
        "candidate_resume_versions",
        sa.column("id", sa.String()),
        sa.column("extraction", sa.JSON()),
        sa.column("extracted_at", sa.DateTime(timezone=True)),
    )
    for application in bind.execute(sa.select(applications).where(
        applications.c.resume_version_id.is_not(None)
    )).mappings():
        bind.execute(
            sa.update(versions).where(versions.c.id == application["resume_version_id"]).values(
                extraction=_snapshot(dict(application)),
                extracted_at=application["created_at"] or datetime.now(timezone.utc),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate_resume_versions") as batch_op:
        batch_op.drop_column("extracted_at")
        batch_op.drop_column("extraction")
