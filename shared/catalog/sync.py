"""Copy the shared catalog into every service that embeds it.

    python shared/catalog/sync.py           # copy catalog.py + catalog.json
    python shared/catalog/sync.py --check   # exit 1 when a copy differs (CI / pre-commit)

Each service builds its own Docker context, so it cannot import from `shared/`; a test inside every
service compares its copy's sha256 with this directory so the two systems cannot drift apart.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FILES = ("catalog.py", "catalog.json")
TARGETS = (ROOT / "backend" / "app" / "catalog", ROOT / "cv-warehouse" / "backend" / "app" / "catalog")
# The contract tests run inside each service against that service's embedded copy.
TEST_FILE = HERE / "tests" / "test_shared_catalog.py"
TEST_TARGETS = (ROOT / "backend" / "tests", ROOT / "cv-warehouse" / "backend" / "tests")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drifted() -> list[Path]:
    stale = [target / name for target in TARGETS for name in FILES
             if not (target / name).exists() or digest(target / name) != digest(HERE / name)]
    return stale + [target / TEST_FILE.name for target in TEST_TARGETS
                    if not (target / TEST_FILE.name).exists() or digest(target / TEST_FILE.name) != digest(TEST_FILE)]


def main() -> int:
    if "--check" in sys.argv:
        stale = drifted()
        for path in stale:
            print(f"out of sync: {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for target in TARGETS:
        target.mkdir(parents=True, exist_ok=True)
        (target / "__init__.py").touch()
        for name in FILES:
            shutil.copyfile(HERE / name, target / name)
        print(f"synced -> {target.relative_to(ROOT)}")
    for target in TEST_TARGETS:
        shutil.copyfile(TEST_FILE, target / TEST_FILE.name)
        print(f"synced -> {(target / TEST_FILE.name).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
