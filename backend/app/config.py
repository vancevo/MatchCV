from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value")


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    auth_required: bool
    auto_seed: bool
    master_seed_on_start: bool
    cors_origins: tuple[str, ...]
    supabase_url: str
    supabase_jwt_secret: str
    openrouter_api_key: str
    openrouter_model: str
    openrouter_site_url: str
    openrouter_app_title: str
    max_upload_mb: int
    redis_url: str
    queue_name: str
    queue_eager: bool
    task_max_attempts: int
    task_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("APP_ENV", "local").strip().lower()
        if environment not in {"local", "test", "staging", "production"}:
            raise RuntimeError("APP_ENV must be local, test, staging, or production")
        default_origins = "http://localhost:3000,https://talentflow-frontend-yifc.onrender.com"
        origins = tuple(value.strip() for value in os.getenv("CORS_ORIGINS", default_origins).split(",") if value.strip())
        if not origins:
            raise RuntimeError("CORS_ORIGINS must contain at least one origin")

        supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
        auth_required = _boolean("AUTH_REQUIRED", False)
        if auth_required and not (supabase_url or jwt_secret):
            raise RuntimeError("AUTH_REQUIRED=true requires SUPABASE_URL or SUPABASE_JWT_SECRET")

        redis_url = os.getenv("REDIS_URL", "").strip()
        queue_eager = _boolean("QUEUE_EAGER", not bool(redis_url))
        if not queue_eager and not redis_url:
            raise RuntimeError("QUEUE_EAGER=false requires REDIS_URL")

        return cls(
            environment=environment,
            database_url=os.getenv("DATABASE_URL", "").strip(),
            auth_required=auth_required,
            auto_seed=_boolean("AUTO_SEED", True),
            master_seed_on_start=_boolean("MASTER_SEED_ON_START", True),
            cors_origins=origins,
            supabase_url=supabase_url,
            supabase_jwt_secret=jwt_secret,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free").strip(),
            openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", "http://localhost:3000").strip(),
            openrouter_app_title=os.getenv("OPENROUTER_APP_TITLE", "TalentFlow Recruitment Copilot").strip(),
            max_upload_mb=_positive_int("MAX_UPLOAD_MB", 10, 100),
            redis_url=redis_url,
            queue_name=os.getenv("QUEUE_NAME", "talentflow-screening").strip() or "talentflow-screening",
            queue_eager=queue_eager,
            task_max_attempts=_positive_int("TASK_MAX_ATTEMPTS", 3, 10),
            task_timeout_seconds=_positive_int("TASK_TIMEOUT_SECONDS", 120, 3600),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
