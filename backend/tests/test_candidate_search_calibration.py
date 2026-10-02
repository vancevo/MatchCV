from __future__ import annotations

import pytest

from app.candidate_search import CandidateVersionMatch, apply_calibrated_confidence, rank_candidate_versions
from app.candidate_search_calibration import calibrated_probability, fit_platt_calibration


def test_platt_calibration_orders_low_and_high_scores():
    fit = fit_platt_calibration(
        [10, 20, 30, 40, 60, 70, 80, 90],
        [0, 0, 0, 1, 2, 2, 3, 3],
    )

    assert fit.sample_count == 8
    assert fit.positive_count == 4
    assert calibrated_probability(90, fit.parameters()) > calibrated_probability(10, fit.parameters())
    assert 0 <= fit.brier_score <= 1


def test_platt_calibration_rejects_one_class_data():
    with pytest.raises(ValueError, match="positive and negative"):
        fit_platt_calibration([10, 20], [0, 1])


def test_exposed_confidence_is_a_probability_not_a_ranking_score():
    ranked = rank_candidate_versions([
        CandidateVersionMatch("candidate", "version", 1, 0.8),
    ])
    calibrated = apply_calibrated_confidence(ranked, {"slope": 2.0, "intercept": -1.0})

    assert calibrated[0].confidence == pytest.approx(
        calibrated_probability(ranked[0].score, {"slope": 2.0, "intercept": -1.0}),
        abs=1e-4,
    )
    assert 0 <= calibrated[0].confidence <= 1
