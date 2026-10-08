"""Score `extract_jd_requirements` against the hand-labelled gold set in jd_gold.json.

The set has two splits: "dev" (written while building the extractor) and "holdout" (written afterwards,
looking only at the text, and scored before any fix was made for it). Both are reported.

    python shared/catalog/evals/run_jd_eval.py            # summary + per-JD diffs
    python shared/catalog/evals/run_jd_eval.py --quiet    # summary only
    python shared/catalog/evals/run_jd_eval.py --json     # machine-readable summary

Skill precision/recall are micro-averaged over (JD, skill) pairs. Fields a JD does not label
(level, education, certifications, languages, domains) are skipped for that JD.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import catalog  # noqa: E402


def prf(tp: int, fp: int, fn: int) -> tuple[float, float]:
    return (tp / (tp + fp) if tp + fp else 1.0, tp / (tp + fn) if tp + fn else 1.0)


def score(gold: list[dict]) -> tuple[dict, list[str]]:
    counts = {name: [0, 0, 0] for name in ("required", "preferred", "both", "certifications", "languages")}
    exact = {"minimum_experience": [0, 0], "category": [0, 0], "level": [0, 0], "education": [0, 0]}
    placement = [0, 0]
    diffs: list[str] = []
    for item in gold:
        want, got = item["expected"], catalog.extract_jd_requirements(item["text"])
        technical = {entry.name for entry in catalog.load_catalog().entries if entry.kind in catalog.TECH_KINDS}
        sets = {   # certifications are scored on their own below
            "required": (set(want["required"]), set(got["required_skills"]) & technical),
            "preferred": (set(want["preferred"]), set(got["preferred_skills"]) & technical),
        }
        sets["both"] = (sets["required"][0] | sets["preferred"][0], sets["required"][1] | sets["preferred"][1])
        for name in ("certifications", "languages"):
            if name in want:
                sets[name] = (set(want[name]), set(got[name]))
        notes: list[str] = []
        for name, (expected, actual) in sets.items():
            counts[name][0] += len(expected & actual)
            counts[name][1] += len(actual - expected)
            counts[name][2] += len(expected - actual)
            if expected != actual and name != "both":
                notes.append(f"{name}: missing {sorted(expected - actual)} extra {sorted(actual - expected)}")
        found = sets["both"][0] & sets["both"][1]
        in_required = {s for s in found if (s in want["required"]) == (s in got["required_skills"])}
        placement[0] += len(in_required)
        placement[1] += len(found)
        for field in exact:
            if field in want:
                exact[field][1] += 1
                if want[field] == got[field]:
                    exact[field][0] += 1
                else:
                    notes.append(f"{field}: want {want[field]!r} got {got[field]!r}")
        if notes:
            diffs.append(f"[{item['id']}] " + " | ".join(notes))

    summary = {}
    for name, (tp, fp, fn) in counts.items():
        precision, recall = prf(tp, fp, fn)
        summary[name] = {"precision": round(precision, 3), "recall": round(recall, 3), "tp": tp, "fp": fp, "fn": fn}
    summary["required_vs_preferred_accuracy"] = round(placement[0] / placement[1], 3) if placement[1] else 1.0
    for field, (ok, total) in exact.items():
        summary[field] = {"exact": round(ok / total, 3) if total else None, "n": total}
    summary["jds"] = len(gold)
    return summary, diffs


def show(title: str, summary: dict, diffs: list[str], quiet: bool) -> None:
    print(f"== {title}: {summary['jds']} JDs")
    for name in ("required", "preferred", "both", "certifications", "languages"):
        row = summary[name]
        print(f"  {name:15s} precision {row['precision']:.3f}  recall {row['recall']:.3f}  (tp {row['tp']} fp {row['fp']} fn {row['fn']})")
    print(f"  required-vs-preferred placement accuracy {summary['required_vs_preferred_accuracy']:.3f}")
    for field in ("minimum_experience", "category", "level", "education"):
        print(f"  {field:20s} exact {summary[field]['exact']}  (n={summary[field]['n']})")
    if diffs and not quiet:
        print("  per-JD differences:")
        print("\n".join("    " + line for line in diffs))


def main() -> int:
    gold = json.loads((HERE / "jd_gold.json").read_text(encoding="utf-8"))
    splits = {name: [item for item in gold if item.get("split", "dev") == name] for name in ("dev", "holdout")}
    results = {name: score(items) for name, items in splits.items() if items}
    results["all"] = score(gold)
    if "--json" in sys.argv:
        print(json.dumps({"catalog_version": catalog.catalog_version(),
                          **{name: summary for name, (summary, _) in results.items()}}, ensure_ascii=False, indent=1))
        return 0
    print(f"catalog {catalog.catalog_version()}")
    for name, (summary, diffs) in results.items():
        show(name, summary, diffs, "--quiet" in sys.argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
