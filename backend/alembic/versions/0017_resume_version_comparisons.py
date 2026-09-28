"""Add skill taxonomy and structured CV version comparisons.

Revision ID: 0017
Revises: 0016
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SKILLS = [
    ("10000000-0000-0000-0000-000000000001", "React", "react", "FRONTEND"),
    ("10000000-0000-0000-0000-000000000002", "Node.js", "nodejs", "BACKEND"),
    ("10000000-0000-0000-0000-000000000003", "PostgreSQL", "postgresql", "DATABASE"),
    ("10000000-0000-0000-0000-000000000004", "JavaScript", "javascript", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000005", "TypeScript", "typescript", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000006", "Python", "python", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000007", "C", "c", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000008", "C++", "c++", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000009", "C#", "c#", "LANGUAGE"),
    ("10000000-0000-0000-0000-000000000010", ".NET", "dotnet", "FRAMEWORK"),
    ("10000000-0000-0000-0000-000000000011", "React Native", "reactnative", "MOBILE"),
    ("10000000-0000-0000-0000-000000000012", "Java", "java", "LANGUAGE"),
]

ALIASES = [
    ("ReactJS", "reactjs", SKILLS[0][0]), ("React.js", "reactjsdot", SKILLS[0][0]),
    ("React JS", "reactjsspace", SKILLS[0][0]),
    ("NodeJS", "nodejsalias", SKILLS[1][0]), ("Node JS", "nodejsspace", SKILLS[1][0]),
    ("Postgres", "postgres", SKILLS[2][0]), ("Postgre SQL", "postgresqlspace", SKILLS[2][0]),
    ("JS", "js", SKILLS[3][0]), ("ECMAScript", "ecmascript", SKILLS[3][0]),
    ("TS", "ts", SKILLS[4][0]), ("Dotnet", "dotnetalias", SKILLS[9][0]),
]


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("canonical_name", sa.String(120), nullable=False, unique=True),
        sa.Column("normalized_name", sa.String(120), nullable=False, unique=True),
        sa.Column("category", sa.String(80), nullable=False, server_default="OTHER"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skills_normalized_name", "skills", ["normalized_name"])
    op.create_table(
        "skill_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("skill_id", sa.String(36), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(120), nullable=False),
        sa.Column("normalized_alias", sa.String(120), nullable=False, unique=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="SEED"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_aliases_skill_id", "skill_aliases", ["skill_id"])
    op.create_index("ix_skill_aliases_normalized_alias", "skill_aliases", ["normalized_alias"])
    op.create_index("ix_skill_aliases_approved", "skill_aliases", ["approved"])

    skills_table = sa.table("skills", sa.column("id"), sa.column("canonical_name"),
                            sa.column("normalized_name"), sa.column("category"))
    op.bulk_insert(skills_table, [
        {"id": item[0], "canonical_name": item[1], "normalized_name": item[2], "category": item[3]}
        for item in SKILLS
    ])
    aliases_table = sa.table("skill_aliases", sa.column("id"), sa.column("skill_id"),
                             sa.column("alias"), sa.column("normalized_alias"), sa.column("source"),
                             sa.column("confidence"), sa.column("approved"))
    # Normalized alias values match resume_comparison.normalize_skill. Suffixes above only keep
    # seed tuples readable while allowing this migration to stay data-only.
    normalized = {
        "ReactJS": "reactjs", "React.js": "reactjs", "React JS": "reactjs",
        "NodeJS": "nodejs", "Node JS": "nodejs", "Postgres": "postgres",
        "Postgre SQL": "postgresql", "JS": "js", "ECMAScript": "ecmascript",
        "TS": "ts", "Dotnet": "dotnet",
    }
    unique_aliases: dict[str, tuple[str, str]] = {}
    for alias, _placeholder, skill_id in ALIASES:
        unique_aliases.setdefault(normalized[alias], (alias, skill_id))
    op.bulk_insert(aliases_table, [
        {"id": f"20000000-0000-0000-0000-{index:012d}", "skill_id": skill_id,
         "alias": alias, "normalized_alias": normalized_alias,
         "source": "SEED", "confidence": 1.0, "approved": True}
        for index, (normalized_alias, (alias, skill_id)) in enumerate(unique_aliases.items(), 1)
    ])

    op.create_table(
        "resume_version_skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resume_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", sa.String(36), sa.ForeignKey("skills.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_key", sa.String(140), nullable=False),
        sa.Column("canonical_name", sa.String(120), nullable=False),
        sa.Column("raw_value", sa.String(120), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("match_method", sa.String(32), nullable=False, server_default="UNCLASSIFIED"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("resume_version_id", "canonical_key", name="uq_resume_version_canonical_skill"),
    )
    op.create_index("ix_resume_version_skills_resume_version_id", "resume_version_skills", ["resume_version_id"])
    op.create_index("ix_resume_version_skills_skill_id", "resume_version_skills", ["skill_id"])
    op.create_index("ix_resume_version_skills_canonical_key", "resume_version_skills", ["canonical_key"])

    op.create_table(
        "resume_version_comparisons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("candidate_profile_id", sa.String(36), sa.ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_version_id", sa.String(36), sa.ForeignKey("candidate_resume_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("removed", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("modified", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("unchanged", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("conflicts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("analysis_method", sa.String(80), nullable=False, server_default="STRUCTURED_DIFF"),
        sa.Column("model_name", sa.String(160), nullable=True),
        sa.Column("model_version", sa.String(80), nullable=False, server_default="cv-diff-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_version_id", "to_version_id", name="uq_resume_version_comparison_pair"),
    )
    op.create_index("ix_resume_version_comparisons_owner_id", "resume_version_comparisons", ["owner_id"])
    op.create_index("ix_resume_version_comparisons_candidate_profile_id", "resume_version_comparisons", ["candidate_profile_id"])
    op.create_index("ix_resume_version_comparisons_from_version_id", "resume_version_comparisons", ["from_version_id"])
    op.create_index("ix_resume_version_comparisons_to_version_id", "resume_version_comparisons", ["to_version_id"])
    op.create_index("ix_resume_version_comparisons_created_at", "resume_version_comparisons", ["created_at"])


def downgrade() -> None:
    op.drop_table("resume_version_comparisons")
    op.drop_table("resume_version_skills")
    op.drop_table("skill_aliases")
    op.drop_table("skills")
