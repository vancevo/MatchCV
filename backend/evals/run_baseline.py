from __future__ import annotations

import json
from pathlib import Path

from app.pipeline import extract_requirements, screen_candidate


DATASET = Path(__file__).with_name("cases.json")


def evaluate() -> dict[str, float | int]:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    requirement_fields = 0
    requirement_matches = 0
    evidence_items = 0
    supported_evidence = 0
    ranking_matches = 0

    for case in cases:
        requirements = extract_requirements(case["job_description"])
        for field, expected in case["expected_requirements"].items():
            requirement_fields += 1
            requirement_matches += requirements[field] == expected

        ranked: list[tuple[str, float]] = []
        for candidate in case["candidates"]:
            result = screen_candidate(candidate["resume_text"], requirements)
            ranked.append((candidate["id"], result["final_score"]))
            for evidence in result["evidence"]:
                evidence_items += 1
                if not evidence["matched"] or evidence["evidence"].casefold() in candidate["resume_text"].casefold():
                    supported_evidence += 1
        ranked.sort(key=lambda item: item[1], reverse=True)
        ranking_matches += ranked[0][0] == case["expected_top_candidate"]

    return {
        "cases": len(cases),
        "requirement_accuracy": round(requirement_matches / requirement_fields, 3),
        "evidence_support_rate": round(supported_evidence / evidence_items, 3),
        "top_1_ranking_accuracy": round(ranking_matches / len(cases), 3),
    }


if __name__ == "__main__":
    metrics = evaluate()
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if min(metrics["requirement_accuracy"], metrics["evidence_support_rate"], metrics["top_1_ranking_accuracy"]) < 1:
        raise SystemExit(1)
