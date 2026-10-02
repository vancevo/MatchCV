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
# config.py's module-level load_dotenv() reads the developer's real backend/.env (override=False,
# so it only fills in whatever isn't already pinned above) - without this, a real COLAB_LLM_ENDPOINT_URL
# set there leaks into the test run and tests make real network calls to a developer's live tunnel.
os.environ["INTERVIEW_ANALYSIS_MODELS"] = (
    "test/model-a:free,test/model-b:free,test/model-c:free,test/model-d:free"
)
os.environ["COLAB_LLM_ENDPOINT_URL"] = ""
os.environ["COLAB_LLM_MODEL"] = ""
os.environ["RERANKER_ENABLED"] = "false"

config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
command.upgrade(config, "head")

from app.main import ensure_local_hr_membership, seed  # noqa: E402

ensure_local_hr_membership()
seed()


def pytest_sessionfinish() -> None:
    Path(_database_file.name).unlink(missing_ok=True)
