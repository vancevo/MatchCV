"""Keep the `skills` / `skill_aliases` tables in step with the shared catalog.

The catalog (shared/catalog) is the one vocabulary TalentFlow and the CV warehouse agree on. This module
loads it into the tables search and CV comparison read, and gives the rest of the backend one place to turn
whatever a user typed (a name, an alias, a catalog id, a database id) into a catalog id.
"""
from __future__ import annotations

from typing import Iterable
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from .catalog import catalog as shared
from .resume_comparison import normalize_skill

SKILL_KINDS = shared.TECH_KINDS | {"soft_skill", "certification"}
RAW_PREFIX = "raw:"

_skills = sa.table(
    "skills", sa.column("id"), sa.column("canonical_name"), sa.column("normalized_name"),
    sa.column("category"), sa.column("catalog_id"),
)
_aliases = sa.table(
    "skill_aliases", sa.column("id"), sa.column("skill_id"), sa.column("alias"),
    sa.column("normalized_alias"), sa.column("source"), sa.column("confidence"), sa.column("approved"),
)


def catalog_skills() -> list[shared.Entry]:
    return shared.load_catalog().entries_of(SKILL_KINDS)


def skill_row_id(catalog_id: str) -> str:
    """Deterministic primary key, so every database seeded from the same catalog agrees on it."""
    return str(uuid5(NAMESPACE_URL, f"talentflow:skill:{catalog_id}"))


def seed_skill_catalog(bind) -> dict[str, int]:
    """Idempotent upsert of catalog skills and aliases through a connection or session.

    Existing rows are matched by normalized name and only gain their catalog id; aliases already taken
    by another skill are skipped (the alias table is unique on the normalized alias).
    """
    skills = {row.normalized_name: row for row in bind.execute(sa.select(_skills))}
    taken = {row.normalized_alias for row in bind.execute(sa.select(_aliases.c.normalized_alias))}
    new_skills: list[dict] = []
    new_aliases: list[dict] = []
    linked = 0
    for entry in catalog_skills():
        key = normalize_skill(entry.name)
        row = skills.get(key)
        if row is None:
            skill_id = skill_row_id(entry.id)
            new_skills.append({"id": skill_id, "canonical_name": entry.name, "normalized_name": key,
                               "category": entry.categories[0] if entry.categories else entry.kind.upper(),
                               "catalog_id": entry.id})
            skills[key] = type("Row", (), {"id": skill_id, "catalog_id": entry.id})()
            taken.add(key)
        else:
            skill_id = row.id
            if row.catalog_id != entry.id:
                bind.execute(sa.update(_skills).where(_skills.c.id == row.id).values(catalog_id=entry.id))
                linked += 1
        for alias in entry.aliases:
            normalized = normalize_skill(alias)
            if normalized and normalized not in taken and normalized not in skills:
                taken.add(normalized)
                new_aliases.append({"id": str(uuid5(NAMESPACE_URL, f"talentflow:alias:{entry.id}:{normalized}")),
                                    "skill_id": skill_id, "alias": alias, "normalized_alias": normalized,
                                    "source": "CATALOG", "confidence": 1.0, "approved": True})
    if new_skills:
        bind.execute(sa.insert(_skills), new_skills)
    if new_aliases:
        bind.execute(sa.insert(_aliases), new_aliases)
    return {"skills_added": len(new_skills), "skills_linked": linked, "aliases_added": len(new_aliases)}


def resolve_filter_keys(db, values: Iterable[str]) -> list[str]:
    """Catalog ids for filter values: names, aliases, catalog ids or ids from the `skills` table."""
    keys: list[str] = []
    for value in values:
        key = skill_key(value)
        if key.startswith(RAW_PREFIX):
            row = db.execute(sa.select(_skills.c.catalog_id).where(_skills.c.id == value.strip())).first()
            if row and row.catalog_id:
                key = row.catalog_id
        if key not in keys and not key.endswith(RAW_PREFIX):
            keys.append(key)
    return keys


def skill_key(value: str) -> str:
    """Catalog id for a name / alias / id, or a raw casefolded key for text the catalog does not know."""
    entry = shared.load_catalog().resolve(value)
    return entry.id if entry else f"{RAW_PREFIX}{value.strip().casefold()}"


def skill_keys(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(skill_key(value) for value in values if value and value.strip()))


def display_name(key: str) -> str:
    entry = shared.load_catalog().by_id.get(key)
    return entry.name if entry else key.removeprefix(RAW_PREFIX)
