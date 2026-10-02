from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.candidate_search import (
    SEARCH_SCORE_VERSION,
    CandidateVersionMatch,
    apply_reranker_scores,
    cosine_similarity,
    rank_candidate_versions,
)
from app.embedding_service import EmbeddingService
from app.reranker_service import RerankerService
from evals.candidate_search.metrics import mean, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank


DEFAULT_DATASET = Path(__file__).with_name("gold_queries.seed.json")


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"\w+", value.casefold(), flags=re.UNICODE) if len(token) > 1}


def _lexical_score(query: str, document: str) -> float:
    query_tokens = _tokens(query)
    return len(query_tokens & _tokens(document)) / max(1, len(query_tokens))


def _validate_dataset(payload: dict[str, Any]) -> None:
    if not payload.get("dataset_version") or not payload.get("corpus") or not payload.get("queries"):
        raise ValueError("Dataset must contain dataset_version, corpus and queries")
    corpus_ids = {item["id"] for item in payload["corpus"]}
    if len(corpus_ids) != len(payload["corpus"]):
        raise ValueError("Corpus IDs must be unique")
    for query in payload["queries"]:
        unknown = set(query["relevance"]) - corpus_ids
        if unknown:
            raise ValueError(f"Query {query['id']} references unknown corpus IDs: {sorted(unknown)}")
        if any(not isinstance(value, int) or value < 0 or value > 3 for value in query["relevance"].values()):
            raise ValueError(f"Query {query['id']} contains relevance outside 0..3")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def evaluate(dataset: dict[str, Any], *, mode: str, service: EmbeddingService | None = None,
             reranker: RerankerService | None = None) -> dict[str, Any]:
    _validate_dataset(dataset)
    corpus = dataset["corpus"]
    documents = [item["document"] for item in corpus]
    runtime = service
    document_vectors: list[list[float]] = []
    if mode in {"dense", "hybrid", "reranked"}:
        runtime = runtime or EmbeddingService()
        document_vectors = runtime.embed_documents(documents)
    reranker_runtime = reranker or (RerankerService() if mode == "reranked" else None)

    rows: list[dict[str, Any]] = []
    violations = 0
    returned = 0
    for query in dataset["queries"]:
        started = time.perf_counter()
        lexical_scores = [_lexical_score(query["text"], document) for document in documents]
        if mode in {"dense", "hybrid", "reranked"}:
            assert runtime is not None
            query_vector = runtime.embed_query(query["text"])
            similarities = [cosine_similarity(query_vector, vector) for vector in document_vectors]
        elif mode == "keyword":
            similarities = [value * 2 - 1 for value in lexical_scores]
        else:
            raise ValueError("mode must be dense, keyword, hybrid or reranked")

        matches = [CandidateVersionMatch(
            candidate_profile_id=item["id"], resume_version_id=item["id"], version_number=1,
            semantic_similarity=similarity,
            lexical_overlap=(lexical_scores[index] if mode in {"keyword", "hybrid", "reranked"} else None),
            skill_ids=frozenset(skill.casefold() for skill in item.get("skills", [])),
            experience_years=float(item.get("experience_years", 0)),
            submitted_at=datetime.now(timezone.utc),
        ) for index, (item, similarity) in enumerate(zip(corpus, similarities))]
        required = [value.casefold() for value in query.get("required_skills", [])]
        preferred = [value.casefold() for value in query.get("preferred_skills", [])]
        minimum_experience = query.get("minimum_experience")
        ranked = rank_candidate_versions(
            matches,
            required_skill_ids=required,
            preferred_skill_ids=preferred,
            minimum_experience=minimum_experience,
            required_skills_are_hard_filter=bool(required),
            minimum_experience_is_hard_filter=minimum_experience is not None,
            limit=len(corpus),
        )
        if mode == "reranked" and ranked:
            assert reranker_runtime is not None
            documents_by_id = {item["id"]: item["document"] for item in corpus}
            reranker_scores = reranker_runtime.score_pairs(
                query["text"], [documents_by_id[item.candidate_profile_id] for item in ranked],
            )
            ranked = apply_reranker_scores(
                ranked, reranker_scores, weight=reranker_runtime.config.weight,
            )
        ranked_ids = [item.candidate_profile_id for item in ranked]
        latency_ms = (time.perf_counter() - started) * 1000
        by_id = {item["id"]: item for item in corpus}
        for item_id in ranked_ids:
            item = by_id[item_id]
            returned += 1
            if required and not set(required).issubset({skill.casefold() for skill in item.get("skills", [])}):
                violations += 1
            if minimum_experience is not None and item.get("experience_years", 0) < minimum_experience:
                violations += 1
        relevance = query["relevance"]
        rows.append({
            "query_id": query["id"],
            "language": query["language"],
            "role_family": query["role_family"],
            "precision_at_5": precision_at_k(ranked_ids, relevance, 5),
            "recall_at_10": recall_at_k(ranked_ids, relevance, 10),
            "recall_at_20": recall_at_k(ranked_ids, relevance, 20),
            "mrr": reciprocal_rank(ranked_ids, relevance),
            "ndcg_at_10": ndcg_at_k(ranked_ids, relevance, 10),
            "latency_ms": round(latency_ms, 3),
            "ranked_ids": ranked_ids,
        })

    metric_names = ("precision_at_5", "recall_at_10", "recall_at_20", "mrr", "ndcg_at_10")
    aggregate = {name: round(mean([row[name] for row in rows]), 4) for name in metric_names}
    aggregate["hard_filter_violation_rate"] = round(violations / returned, 4) if returned else 0.0
    latencies = [row["latency_ms"] for row in rows]
    aggregate["latency_ms_p50"] = round(_percentile(latencies, 0.50), 3)
    aggregate["latency_ms_p95"] = round(_percentile(latencies, 0.95), 3)

    slices: dict[str, dict[str, dict[str, float]]] = {}
    for dimension in ("language", "role_family"):
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row[dimension]].append(row)
        slices[dimension] = {
            key: {name: round(mean([row[name] for row in values]), 4) for name in metric_names}
            for key, values in sorted(grouped.items())
        }

    status = runtime.status() if runtime is not None else None
    return {
        "dataset_version": dataset["dataset_version"],
        "dataset_kind": "SYNTHETIC_SEED" if ".seed." in dataset["dataset_version"] else "GOLD",
        "mode": mode,
        "score_version": SEARCH_SCORE_VERSION,
        "model": status,
        "reranker": reranker_runtime.status() if reranker_runtime is not None else None,
        "query_count": len(rows),
        "corpus_count": len(corpus),
        "metrics": aggregate,
        "slices": slices,
        "queries": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=("dense", "keyword", "hybrid", "reranked"), default="dense")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate(dataset, mode=args.mode)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
