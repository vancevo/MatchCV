"""Precision@10 of candidate search against the CV warehouse's own labels.

    python shared/catalog/evals/run_search_eval.py                 # all three systems
    python shared/catalog/evals/run_search_eval.py --k 5 --json

Ground truth: the 500 synthetic CVs are named `<CATEGORY>_<n>_<LANG>.pdf`, and the prefix is the category the CV
was written for. A result is relevant when its e-mail belongs to a CV of the query's category. Systems:

  talentflow   POST /api/candidate-profiles/search  (TalentFlow, the internal talent pool; the category the
               query targets is a soft boost)
  tf-noboost   the same with filters.auto_specialization=false: retrieval and skill coverage alone
  wh-off       POST /api/v1/cvs/search with filters.auto_specialization=false (ranking alone)
  wh-auto      the same with auto_specialization=true (the query's category becomes a hard filter)

Environment: TALENTFLOW_URL (http://localhost:8000), CV_WAREHOUSE_URL (http://localhost:8100),
CV_WAREHOUSE_API_KEY (the key in cv-warehouse/backend/.env; local default shown below).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALENTFLOW = os.getenv("TALENTFLOW_URL", "http://localhost:8000").rstrip("/")
WAREHOUSE = os.getenv("CV_WAREHOUSE_URL", "http://localhost:8100").rstrip("/")
API_KEY = os.getenv("CV_WAREHOUSE_API_KEY", "replace-with-a-long-random-key")
PREFIX_TO_CATEGORY = {
    "AI_ML": "AI_ML", "Data_Engineering": "DATA_ENGINEERING", "Data_Analytics_BI": "DATA_ANALYTICS_BI",
    "Cybersecurity": "CYBERSECURITY", "Cloud_Engineering": "CLOUD", "DevOps_SRE": "DEVOPS_SRE",
    "Backend": "BACKEND", "Fullstack": "FULLSTACK", "Frontend": "FRONTEND", "Mobile": "MOBILE", "QA_Automation": "QA_AUTOMATION",
}
SYSTEMS = ("talentflow", "tf-noboost", "wh-off", "wh-auto")


def call(url: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def warehouse_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def ground_truth() -> dict[str, str]:
    """e-mail -> category code, from the warehouse's file names."""
    labels: dict[str, str] = {}
    offset = 0
    while True:
        page = call(f"{WAREHOUSE}/api/v1/cvs?limit=100&offset={offset}", headers=warehouse_headers())["items"]
        for item in page:
            prefix = item["original_filename"].rsplit("_", 2)[0]
            if prefix in PREFIX_TO_CATEGORY and item["email"]:
                labels[item["email"].casefold()] = PREFIX_TO_CATEGORY[prefix]
        if len(page) < 100:
            return labels
        offset += len(page)


def search(system: str, query: str, k: int) -> list[str]:
    """E-mails of the top results, best first."""
    if system in {"talentflow", "tf-noboost"}:
        filters = {"auto_specialization": system == "talentflow"}
        body = call(f"{TALENTFLOW}/api/candidate-profiles/search", {"query": query, "limit": k, "filters": filters})
        return [item["candidate_profile"]["email"].casefold() for item in body["results"]]
    filters = {"auto_specialization": system == "wh-auto"}
    body = call(f"{WAREHOUSE}/api/v1/cvs/search", {"query": query, "filters": filters, "limit": k}, warehouse_headers())
    return [item["email"].casefold() for item in body["results"]]


def main() -> int:
    k = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 10
    queries = json.loads((HERE / "search_queries.json").read_text(encoding="utf-8"))
    labels = ground_truth()
    hits: dict[str, dict[str, list[float]]] = {system: defaultdict(list) for system in SYSTEMS}
    rows = []
    for item in queries:
        row = {"id": item["id"], "category": item["category"]}
        for system in SYSTEMS:
            emails = search(system, item["query"], k)
            relevant = sum(labels.get(email) == item["category"] for email in emails)
            hits[system][item["category"]].append(relevant / k)
            row[system] = relevant
        rows.append(row)
    summary = {}
    for system in SYSTEMS:
        per_category = {category: sum(values) / len(values) for category, values in hits[system].items()}
        every = [value for values in hits[system].values() for value in values]
        summary[system] = {"overall": sum(every) / len(every), "per_category": per_category,
                           "lowest_category": min(per_category.values())}
    if "--json" in sys.argv:
        print(json.dumps({"k": k, "queries": len(queries), "summary": summary, "rows": rows}, indent=1))
        return 0
    print(f"precision@{k} over {len(queries)} queries (relevant = same category as the query)")
    categories = sorted({item["category"] for item in queries})
    print(f"{'category':20s}" + "".join(f"{system:>12s}" for system in SYSTEMS))
    for category in categories:
        print(f"{category:20s}" + "".join(f"{summary[system]['per_category'][category]:12.0%}" for system in SYSTEMS))
    print(f"{'OVERALL':20s}" + "".join(f"{summary[system]['overall']:12.1%}" for system in SYSTEMS))
    print(f"{'lowest category':20s}" + "".join(f"{summary[system]['lowest_category']:12.0%}" for system in SYSTEMS))
    weak = [row for row in rows if row["talentflow"] < k * 0.7]
    if weak:
        print("\nqueries where TalentFlow found fewer than 70% relevant: " +
              ", ".join(f"{row['id']} ({row['talentflow']}/{k})" for row in weak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
