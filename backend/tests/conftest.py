from __future__ import annotations

import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config


_database_file = tempfile.NamedTemporaryFile(prefix="talentflow-tests-", suffix=".db", delete=False)
_database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_database_file.name}"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["AUTH_REQUIRED"] = "false"
os.environ["AUTO_SEED"] = "true"
os.environ["APP_ENV"] = "test"

config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
command.upgrade(config, "head")

from app.main import seed  # noqa: E402

seed()


def pytest_sessionfinish() -> None:
    Path(_database_file.name).unlink(missing_ok=True)
