from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    auth_required: bool
    supabase_url: str
    supabase_jwt_secret: str
    storage_dir: str
    max_upload_mb: int
    embedding_enabled: bool
    embedding_model: str
    embedding_model_path: str
    reranker_enabled: bool
    reranker_model: str
    reranker_model_path: str
    bootstrap_api_key: str
    default_tenant_id: str
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./cv_warehouse.db").strip(),
            auth_required=_bool("AUTH_REQUIRED", False),
            supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_jwt_secret=os.getenv("SUPABASE_JWT_SECRET", "").strip(),
            storage_dir=os.getenv("CV_STORAGE_DIR", "uploads").strip() or "uploads",
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "10")),
            embedding_enabled=_bool("EMBEDDING_ENABLED", False),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip(),
            embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH", "models/bge-m3").strip(),
            reranker_enabled=_bool("RERANKER_ENABLED", False),
            reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip(),
            reranker_model_path=os.getenv("RERANKER_MODEL_PATH", "models/bge-reranker-v2-m3").strip(),
            bootstrap_api_key=os.getenv("CV_WAREHOUSE_API_KEY", "").strip(),
            default_tenant_id=os.getenv("DEFAULT_TENANT_ID", "local-tenant").strip() or "local-tenant",
            cors_origins=tuple(
                value.strip() for value in os.getenv(
                    "CORS_ORIGINS", "http://localhost:3100,http://localhost:3000"
                ).split(",") if value.strip()
            ),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
