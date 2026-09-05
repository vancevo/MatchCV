from evals.run_baseline import evaluate


def test_deterministic_baseline():
    metrics = evaluate()
    assert metrics == {
        "cases": 2,
        "requirement_accuracy": 1.0,
        "evidence_support_rate": 1.0,
        "top_1_ranking_accuracy": 1.0,
        "manual_review_routing_accuracy": 1.0,
    }
