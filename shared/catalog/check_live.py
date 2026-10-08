"""Compare the catalog the two RUNNING services loaded.

    python shared/catalog/check_live.py        # exit 1 when the services (or the files here) disagree

Reads TalentFlow's /api/catalog and the warehouse's /api/v1/catalog; each reports the sha256 of the
catalog.json and catalog.py it loaded, so a service started before a sync shows up here.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URLS = {
    "talentflow": os.getenv("TALENTFLOW_URL", "http://localhost:8000").rstrip("/") + "/api/catalog",
    "warehouse": os.getenv("CV_WAREHOUSE_URL", "http://localhost:8100").rstrip("/") + "/api/v1/catalog",
}


def main() -> int:
    prints = {"source files": {"version": json.loads((HERE / "catalog.json").read_text())["version"],
                               "json_sha256": hashlib.sha256((HERE / "catalog.json").read_bytes()).hexdigest(),
                               "py_sha256": hashlib.sha256((HERE / "catalog.py").read_bytes()).hexdigest()}}
    for name, url in URLS.items():
        with urllib.request.urlopen(url, timeout=10) as response:
            prints[name] = json.load(response)["fingerprint"]
    reference = prints["source files"]
    ok = True
    for name, value in prints.items():
        same = value == reference
        ok &= same
        print(f"{name:13s} {value['version']}  json {value['json_sha256'][:12]}  py {value['py_sha256'][:12]}  {'ok' if same else 'DIFFERENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
