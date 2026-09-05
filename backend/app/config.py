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
    integration_provider: str
    allow_eager_real_integrations: bool
    integration_token_secret: str
    oauth_state_secret: str
    public_app_url: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    microsoft_client_id: str
    microsoft_client_secret: str
    microsoft_redirect_uri: str
    provider_webhook_secret: str
    operational_sweep_interval_minutes: int

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

        integration_provider = os.getenv("INTEGRATION_PROVIDER", "local").strip().lower()
        if integration_provider not in {"local", "google", "microsoft"}:
            raise RuntimeError("INTEGRATION_PROVIDER must be local, google, or microsoft")
        allow_eager_real_integrations = _boolean("ALLOW_EAGER_REAL_INTEGRATIONS", False)
        token_secret = os.getenv("INTEGRATION_TOKEN_SECRET", jwt_secret or "talentflow-local-token-key").strip()
        oauth_state_secret = os.getenv("OAUTH_STATE_SECRET", jwt_secret or "talentflow-local-oauth-state").strip()
        if environment in {"staging", "production"} and integration_provider != "local":
            if token_secret == "talentflow-local-token-key" or oauth_state_secret == "talentflow-local-oauth-state":
                raise RuntimeError("Real integrations require INTEGRATION_TOKEN_SECRET and OAUTH_STATE_SECRET")
            if queue_eager and not allow_eager_real_integrations:
                raise RuntimeError(
                    "Real integrations in staging/production require QUEUE_EAGER=false, "
                    "or ALLOW_EAGER_REAL_INTEGRATIONS=true for free/demo deployments"
                )

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
            integration_provider=integration_provider,
            allow_eager_real_integrations=allow_eager_real_integrations,
            integration_token_secret=token_secret,
            oauth_state_secret=oauth_state_secret,
            public_app_url=os.getenv("PUBLIC_APP_URL", "http://localhost:3000").strip().rstrip("/"),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/integrations/google/callback").strip(),
            microsoft_client_id=os.getenv("MICROSOFT_CLIENT_ID", "").strip(),
            microsoft_client_secret=os.getenv("MICROSOFT_CLIENT_SECRET", "").strip(),
            microsoft_redirect_uri=os.getenv("MICROSOFT_REDIRECT_URI", "http://localhost:8000/api/integrations/microsoft/callback").strip(),
            provider_webhook_secret=os.getenv("PROVIDER_WEBHOOK_SECRET", "").strip(),
            operational_sweep_interval_minutes=_positive_int("OPERATIONAL_SWEEP_INTERVAL_MINUTES", 15, 1440),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
