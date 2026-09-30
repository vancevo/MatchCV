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
os.environ["TALENTFLOW_MODEL_ENABLED"] = "false"
os.environ["INTEGRATION_PROVIDER"] = "local"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["PROVIDER_WEBHOOK_SECRET"] = ""
os.environ["QUEUE_EAGER"] = "true"
os.environ["AUTH_REQUIRED"] = "false"
os.environ["AUTO_SEED"] = "true"
os.environ["APP_ENV"] = "test"
os.environ["EMBEDDING_ENABLED"] = "false"

config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
command.upgrade(config, "head")

from app.main import seed  # noqa: E402

seed()


def pytest_sessionfinish() -> None:
    Path(_database_file.name).unlink(missing_ok=True)
