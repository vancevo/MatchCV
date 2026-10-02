"""Small dependency-free Platt calibrator for human-labeled search results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


CALIBRATION_METHOD = "PLATT"


@dataclass(frozen=True)
class CalibrationFit:
    slope: float
    intercept: float
    sample_count: int
    positive_count: int
    brier_score: float
    log_loss: float

    def parameters(self) -> dict[str, float]:
        return {"slope": self.slope, "intercept": self.intercept}

    def metrics(self) -> dict[str, float | int]:
        return {
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "positive_count": self.positive_count,
            "negative_count": self.sample_count - self.positive_count,
        }


def _sigmoid(value: float) -> float:
    value = max(-50.0, min(50.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def calibrated_probability(score: float, parameters: dict) -> float:
    normalized = min(1.0, max(0.0, float(score) / 100.0))
    return _sigmoid(
        float(parameters.get("slope", 0.0)) * normalized
        + float(parameters.get("intercept", 0.0))
    )


def fit_platt_calibration(
    scores: Sequence[float],
    relevance: Sequence[int],
    *,
    relevant_at: int = 2,
    iterations: int = 2000,
    learning_rate: float = 0.10,
    regularization: float = 0.01,
) -> CalibrationFit:
    if len(scores) != len(relevance) or not scores:
        raise ValueError("Calibration scores and labels must be non-empty and aligned")
    labels = [1.0 if int(value) >= relevant_at else 0.0 for value in relevance]
    positives = int(sum(labels))
    if positives == 0 or positives == len(labels):
        raise ValueError("Calibration requires both positive and negative judgments")
    values = [min(1.0, max(0.0, float(score) / 100.0)) for score in scores]
    slope = 1.0
    intercept = math.log((positives + 1) / (len(labels) - positives + 1))
    count = float(len(values))
    for _ in range(iterations):
        gradient_slope = 0.0
        gradient_intercept = 0.0
        for value, label in zip(values, labels):
            error = _sigmoid(slope * value + intercept) - label
            gradient_slope += error * value
            gradient_intercept += error
        gradient_slope = gradient_slope / count + regularization * slope
        gradient_intercept /= count
        slope -= learning_rate * gradient_slope
        intercept -= learning_rate * gradient_intercept
    probabilities = [_sigmoid(slope * value + intercept) for value in values]
    epsilon = 1e-12
    brier = sum((probability - label) ** 2 for probability, label in zip(probabilities, labels)) / count
    log_loss = -sum(
        label * math.log(max(epsilon, probability))
        + (1 - label) * math.log(max(epsilon, 1 - probability))
        for probability, label in zip(probabilities, labels)
    ) / count
    return CalibrationFit(
        slope=round(slope, 8),
        intercept=round(intercept, 8),
        sample_count=len(values),
        positive_count=positives,
        brier_score=round(brier, 8),
        log_loss=round(log_loss, 8),
    )
