from __future__ import annotations

import os
import tempfile
from pathlib import Path


database = tempfile.NamedTemporaryFile(prefix="cv-warehouse-test-", suffix=".db", delete=False)
database.close()
storage = tempfile.mkdtemp(prefix="cv-warehouse-uploads-")
os.environ["DATABASE_URL"] = f"sqlite:///{database.name}"
os.environ["CV_STORAGE_DIR"] = storage
os.environ["AUTH_REQUIRED"] = "false"
os.environ["EMBEDDING_ENABLED"] = "false"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["CV_WAREHOUSE_API_KEY"] = "test-service-key"


def pytest_sessionfinish() -> None:
    Path(database.name).unlink(missing_ok=True)
    for value in Path(storage).rglob("*"):
        if value.is_file():
            value.unlink()
    for value in sorted(Path(storage).rglob("*"), reverse=True):
        if value.is_dir():
            value.rmdir()
    Path(storage).rmdir()
