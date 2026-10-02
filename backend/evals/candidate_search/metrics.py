from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def precision_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, int], k: int,
                   *, relevant_at: int = 2) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    values = ranked_ids[:k]
    return sum(relevance.get(item, 0) >= relevant_at for item in values) / k


def recall_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, int], k: int,
                *, relevant_at: int = 2) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    relevant = {item for item, grade in relevance.items() if grade >= relevant_at}
    if not relevant:
        return 1.0
    return len(relevant & set(ranked_ids[:k])) / len(relevant)


def reciprocal_rank(ranked_ids: Sequence[str], relevance: Mapping[str, int],
                    *, relevant_at: int = 2) -> float:
    for index, item in enumerate(ranked_ids, start=1):
        if relevance.get(item, 0) >= relevant_at:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, int], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")

    def dcg(grades: Sequence[int]) -> float:
        return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))

    actual = dcg([relevance.get(item, 0) for item in ranked_ids[:k]])
    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    return actual / ideal if ideal else 1.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
