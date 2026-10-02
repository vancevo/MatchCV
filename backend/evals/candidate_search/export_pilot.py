"""Export a private, PII-free pilot annotation set from the current database.

This does not create a gold dataset: labels remain empty until recruiters judge
the candidates. The output directory is ignored by Git by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import session_scope
from app.models import Job, ResumeVersionEmbedding


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "private" / "candidate-search.pilot.v1.json"


def _anonymous_id(prefix: str, value: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def export(output: Path, *, owner_id: str | None, salt: str) -> dict:
    with session_scope() as db:
        corpus_query = select(ResumeVersionEmbedding).where(
            ResumeVersionEmbedding.status == "READY",
        )
        job_query = select(Job).where(Job.status == "OPEN")
        if owner_id:
            corpus_query = corpus_query.where(ResumeVersionEmbedding.owner_id == owner_id)
            job_query = job_query.where(Job.owner_id == owner_id)
        entries = list(db.scalars(corpus_query.order_by(ResumeVersionEmbedding.created_at)))
        jobs = list(db.scalars(job_query.order_by(Job.created_at)))

    corpus = [{
        "id": _anonymous_id("cv", item.resume_version_id, salt),
        "document": item.search_document,
        "skills": list(item.canonical_skills or []),
        "experience_years": item.experience_years,
    } for item in entries]
    corpus_ids = [item["id"] for item in corpus]
    queries = []
    for job in jobs:
        requirements = job.requirements if isinstance(job.requirements, dict) else {}
        text_parts = [job.title, job.description]
        text_parts.extend(str(value) for value in requirements.get("required_skills", []) if value)
        text_parts.extend(str(value) for value in requirements.get("preferred_skills", []) if value)
        query_text = ". ".join(value.strip() for value in text_parts if value and value.strip())
        if not query_text:
            continue
        queries.append({
            "id": _anonymous_id("query", job.id, salt),
            "text": query_text,
            "language": "unknown",
            "role_family": job.department or "unknown",
            "required_skills": requirements.get("required_skills", []),
            "preferred_skills": requirements.get("preferred_skills", []),
            "minimum_experience": requirements.get("minimum_experience"),
            "relevance": {candidate_id: None for candidate_id in corpus_ids},
            "annotation_status": "UNREVIEWED",
        })
    payload = {
        "dataset_version": "candidate-search.pilot.v1",
        "dataset_kind": "PRIVATE_UNREVIEWED_PILOT",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label_scale": {"0": "not relevant", "1": "partly relevant", "2": "relevant", "3": "highly relevant"},
        "relevant_at": 2,
        "corpus": corpus,
        "queries": queries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "corpus_count": len(corpus), "query_count": len(queries)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--owner-id")
    args = parser.parse_args()
    salt = os.getenv("CANDIDATE_SEARCH_EVAL_SALT", "local-private-pilot")
    print(json.dumps(export(args.output, owner_id=args.owner_id, salt=salt), ensure_ascii=False))


if __name__ == "__main__":
    main()
