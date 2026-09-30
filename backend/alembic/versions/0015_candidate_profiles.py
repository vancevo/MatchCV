"""Add tenant-scoped candidate profiles and immutable CV versions.

Revision ID: 0015
Revises: 0014
"""
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _email(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    return normalized or None


def _phone(text: str | None) -> str | None:
    match = re.search(r"(?<!\d)(?:\+?84|0)[\d .()-]{8,13}(?!\d)", text or "")
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if digits.startswith("84") and len(digits) in {11, 12}:
        digits = "0" + digits[2:]
    return digits if 9 <= len(digits) <= 11 else None


def _filename(name: str, number: int, original: str | None) -> str:
    safe = re.sub(r"[^\w]+", "_", (name or "Ứng viên").strip(), flags=re.UNICODE).strip("_")[:120]
    suffix = Path(original or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        suffix = ".txt"
    return f"{safe or 'Ung_vien'}_CV_v{number}{suffix}"


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("normalized_email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("normalized_phone", sa.String(20), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "normalized_email", name="uq_candidate_profile_owner_email"),
        sa.UniqueConstraint("owner_id", "normalized_phone", name="uq_candidate_profile_owner_phone"),
    )
    op.create_index("ix_candidate_profiles_owner_id", "candidate_profiles", ["owner_id"])
    op.create_table(
        "candidate_resume_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("candidate_profile_id", sa.String(36), sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("version_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_profile_id", "version_number", name="uq_candidate_resume_profile_version"),
    )
    op.create_index("ix_candidate_resume_versions_owner_id", "candidate_resume_versions", ["owner_id"])
    op.create_index("ix_candidate_resume_versions_candidate_profile_id", "candidate_resume_versions", ["candidate_profile_id"])
    op.create_index("ix_candidate_resume_versions_storage_key", "candidate_resume_versions", ["storage_key"])
    op.create_index("ix_candidate_resume_versions_checksum", "candidate_resume_versions", ["checksum"])
    op.create_index("ix_candidate_resume_versions_submitted_at", "candidate_resume_versions", ["submitted_at"])
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("candidate_profile_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("resume_version_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("candidate_phone", sa.String(40), nullable=False, server_default=""))
        batch_op.create_foreign_key("fk_applications_candidate_profile", "candidate_profiles", ["candidate_profile_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_applications_resume_version", "candidate_resume_versions", ["resume_version_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index("ix_applications_candidate_profile_id", ["candidate_profile_id"])
        batch_op.create_index("ix_applications_resume_version_id", ["resume_version_id"])

    bind = op.get_bind()
    apps = list(bind.execute(sa.text(
        "SELECT id, owner_id, candidate_name, candidate_email, resume_filename, resume_size, "
        "resume_checksum, resume_text, created_at FROM applications ORDER BY created_at, id"
    )).mappings())
    profiles: dict[tuple[str, str], dict] = {}
    versions: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    for app in apps:
        email = _email(app["candidate_email"])
        phone = _phone(app["resume_text"])
        identity = email or phone or f"legacy:{app['id']}"
        key = (app["owner_id"], identity)
        profile = profiles.get(key)
        seen_at = app["created_at"] or now
        if not profile:
            profile = {"id": str(uuid4()), "version": 0}
            profiles[key] = profile
            bind.execute(sa.text(
                "INSERT INTO candidate_profiles "
                "(id, owner_id, full_name, email, normalized_email, phone, normalized_phone, first_seen_at, last_seen_at, created_at, updated_at) "
                "VALUES (:id, :owner_id, :name, :email, :normalized_email, :phone, :normalized_phone, :seen, :seen, :seen, :seen)"
            ), {"id": profile["id"], "owner_id": app["owner_id"], "name": app["candidate_name"],
                "email": app["candidate_email"] or None, "normalized_email": email,
                "phone": phone, "normalized_phone": phone, "seen": seen_at})
        profile["version"] += 1
        version_id = str(uuid4())
        original = app["resume_filename"]
        renamed = _filename(app["candidate_name"], profile["version"], original)
        digest = app["resume_checksum"] or __import__("hashlib").sha256((app["resume_text"] or "").encode()).hexdigest()
        bind.execute(sa.text(
            "INSERT INTO candidate_resume_versions "
            "(id, owner_id, candidate_profile_id, version_number, original_filename, version_filename, storage_key, file_size, checksum, extracted_text, submitted_at, created_at) "
            "VALUES (:id, :owner_id, :profile_id, :number, :original, :renamed, :storage_key, :size, :checksum, :text, :submitted, :submitted)"
        ), {"id": version_id, "owner_id": app["owner_id"], "profile_id": profile["id"],
            "number": profile["version"], "original": original, "renamed": renamed,
            "storage_key": app["id"], "size": app["resume_size"], "checksum": digest,
            "text": app["resume_text"] or "", "submitted": seen_at})
        bind.execute(sa.text(
            "UPDATE applications SET candidate_profile_id=:profile_id, resume_version_id=:version_id, "
            "candidate_phone=:phone, resume_filename=:filename WHERE id=:application_id"
        ), {"profile_id": profile["id"], "version_id": version_id, "phone": phone or "",
            "filename": renamed, "application_id": app["id"]})
        bind.execute(sa.text(
            "UPDATE candidate_profiles SET last_seen_at=:seen, updated_at=:seen WHERE id=:id"
        ), {"seen": seen_at, "id": profile["id"]})


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_index("ix_applications_resume_version_id")
        batch_op.drop_index("ix_applications_candidate_profile_id")
        batch_op.drop_constraint("fk_applications_resume_version", type_="foreignkey")
        batch_op.drop_constraint("fk_applications_candidate_profile", type_="foreignkey")
        batch_op.drop_column("candidate_phone")
        batch_op.drop_column("resume_version_id")
        batch_op.drop_column("candidate_profile_id")
    op.drop_table("candidate_resume_versions")
    op.drop_table("candidate_profiles")
