from __future__ import annotations

import pytest

from evals.candidate_search.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank


RELEVANCE = {"a": 3, "b": 2, "c": 1, "d": 0}


def test_candidate_search_retrieval_metrics_reward_correct_order():
    ranked = ["a", "b", "c", "d"]

    assert precision_at_k(ranked, RELEVANCE, 2) == 1
    assert recall_at_k(ranked, RELEVANCE, 2) == 1
    assert reciprocal_rank(ranked, RELEVANCE) == 1
    assert ndcg_at_k(ranked, RELEVANCE, 4) == pytest.approx(1)


def test_candidate_search_retrieval_metrics_penalize_late_relevant_results():
    ranked = ["d", "c", "b", "a"]

    assert precision_at_k(ranked, RELEVANCE, 2) == 0
    assert recall_at_k(ranked, RELEVANCE, 2) == 0
    assert reciprocal_rank(ranked, RELEVANCE) == pytest.approx(1 / 3)
    assert ndcg_at_k(ranked, RELEVANCE, 4) < 1
