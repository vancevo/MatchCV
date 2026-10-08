"""Canonical-skill helpers shared by search and the facet endpoint (all matching goes through the catalog)."""
from __future__ import annotations

from typing import Iterable

from .catalog import catalog as shared

RAW_PREFIX = "raw:"


def skill_key(value: str) -> str:
    """Canonical catalog id for a skill name / alias / id, or a raw casefolded key for unknown text."""
    entry = shared.load_catalog().resolve(value)
    return entry.id if entry else f"{RAW_PREFIX}{value.strip().casefold()}"


def skill_keys(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(skill_key(value) for value in values if value and value.strip()))


def display_name(key: str) -> str:
    entry = shared.load_catalog().by_id.get(key)
    return entry.name if entry else key.removeprefix(RAW_PREFIX)


def coverage(wanted: list[str], have: set[str]) -> tuple[list[str], list[str]]:
    """(matched, missing) keys, in the order they were asked for."""
    return [key for key in wanted if key in have], [key for key in wanted if key not in have]
