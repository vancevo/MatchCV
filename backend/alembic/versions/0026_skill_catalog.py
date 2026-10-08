"""Link skills to the shared catalog and load the catalog's skills.

The data comes from a frozen snapshot (0026_skill_catalog_seed.json) and the seeding code is inlined, so this
migration builds the same database whatever the application code looks like later. Newer catalog versions are
loaded at startup by app.skill_catalog.seed_skill_catalog.

Revision ID: 0026
Revises: 0025
"""
import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT = Path(__file__).with_name("0026_skill_catalog_seed.json")


def _normalize(value: str) -> str:
    """Same rule as resume_comparison.normalize_skill at the time of this migration."""
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip().casefold())
    protected = {"c++": "c++", "c#": "c#", ".net": "dotnet", "asp.net": "aspdotnet", "node.js": "nodejs"}
    return protected.get(text) or re.sub(r"[\s._-]+", "", text)


def upgrade() -> None:
    op.add_column("skills", sa.Column("catalog_id", sa.String(80), nullable=True))
    op.create_index("ix_skills_catalog_id", "skills", ["catalog_id"])
    bind = op.get_bind()
    skills = sa.table("skills", sa.column("id"), sa.column("canonical_name"), sa.column("normalized_name"),
                      sa.column("category"), sa.column("catalog_id"))
    aliases = sa.table("skill_aliases", sa.column("id"), sa.column("skill_id"), sa.column("alias"),
                       sa.column("normalized_alias"), sa.column("source"), sa.column("confidence"),
                       sa.column("approved"))
    existing = {row.normalized_name: row.id for row in bind.execute(sa.select(skills))}
    taken = {row.normalized_alias for row in bind.execute(sa.select(aliases.c.normalized_alias))}
    new_skills: list[dict] = []
    new_aliases: list[dict] = []
    for item in json.loads(SNAPSHOT.read_text(encoding="utf-8"))["skills"]:
        key = _normalize(item["name"])
        if key in existing:
            bind.execute(sa.update(skills).where(skills.c.id == existing[key]).values(catalog_id=item["id"]))
            skill_id = existing[key]
        else:
            skill_id = str(uuid5(NAMESPACE_URL, f"talentflow:skill:{item['id']}"))
            new_skills.append({"id": skill_id, "canonical_name": item["name"], "normalized_name": key,
                               "category": item["category"], "catalog_id": item["id"]})
            existing[key] = skill_id
            taken.add(key)
        for alias in item["aliases"]:
            normalized = _normalize(alias)
            if normalized and normalized not in taken and normalized not in existing:
                taken.add(normalized)
                new_aliases.append({"id": str(uuid5(NAMESPACE_URL, f"talentflow:alias:{item['id']}:{normalized}")),
                                    "skill_id": skill_id, "alias": alias, "normalized_alias": normalized,
                                    "source": "CATALOG", "confidence": 1.0, "approved": True})
    if new_skills:
        bind.execute(sa.insert(skills), new_skills)
    if new_aliases:
        bind.execute(sa.insert(aliases), new_aliases)


def downgrade() -> None:
    op.drop_index("ix_skills_catalog_id", table_name="skills")
    op.drop_column("skills", "catalog_id")
