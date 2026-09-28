"""Local, process-scoped embedding runtime for candidate semantic search.

The service deliberately never downloads a model while handling a request.  A
deployment must place BGE-M3 on disk first and point ``EMBEDDING_MODEL_PATH``
at it.  Tests and lightweight installations can inject any object implementing
``encode(texts, normalize_embeddings=True)``.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol, Sequence


class Encoder(Protocol):
    def encode(self, texts: Sequence[str], *, normalize_embeddings: bool = True): ...


class EmbeddingUnavailable(RuntimeError):
    """Raised when semantic search was requested but its local model is unavailable."""


@dataclass(frozen=True)
class EmbeddingRuntimeConfig:
    enabled: bool = True
    model_name: str = "BAAI/bge-m3"
    model_path: str = "models/bge-m3"
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    dimension: int = 1024
    normalize: bool = True
    device: str = "cpu"

    @classmethod
    def from_env(cls) -> "EmbeddingRuntimeConfig":
        enabled = os.getenv("EMBEDDING_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        return cls(
            enabled=enabled,
            model_name=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3",
            model_path=os.getenv("EMBEDDING_MODEL_PATH", "models/bge-m3").strip() or "models/bge-m3",
            model_revision=os.getenv(
                "EMBEDDING_MODEL_REVISION", "5617a9f61b028005a4858fdac845db406aefb181"
            ).strip(),
            dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
            normalize=os.getenv("EMBEDDING_NORMALIZE", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            device=os.getenv("EMBEDDING_DEVICE", "cpu").strip() or "cpu",
        )


def _default_loader(config: EmbeddingRuntimeConfig) -> Encoder:
    model_path = Path(config.model_path).expanduser()
    if not model_path.is_dir():
        raise EmbeddingUnavailable(
            f"Embedding model is not installed at {model_path}. "
            "Download BAAI/bge-m3 during setup; runtime downloading is disabled."
        )
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - depends on optional production dependency
        raise EmbeddingUnavailable(
            "sentence-transformers is required for local semantic search"
        ) from exc
    return SentenceTransformer(str(model_path), device=config.device, local_files_only=True)


def _as_vector(value: object) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise RuntimeError("Embedding encoder returned an unsupported value")
    return [float(item) for item in value]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


class EmbeddingService:
    """Lazy singleton-friendly wrapper that loads the encoder at most once."""

    def __init__(
        self,
        config: EmbeddingRuntimeConfig | None = None,
        *,
        encoder: Encoder | None = None,
        loader: Callable[[EmbeddingRuntimeConfig], Encoder] | None = None,
    ) -> None:
        self.config = config or EmbeddingRuntimeConfig.from_env()
        self._encoder = encoder
        self._loader = loader or _default_loader
        self._lock = RLock()
        self._load_attempted = encoder is not None
        self._error: str | None = None

    def _get_encoder(self) -> Encoder:
        if not self.config.enabled:
            raise EmbeddingUnavailable("Semantic embedding is disabled")
        if self._encoder is not None:
            return self._encoder
        with self._lock:
            if self._encoder is not None:
                return self._encoder
            if self._load_attempted:
                raise EmbeddingUnavailable(self._error or "Embedding model is unavailable")
            self._load_attempted = True
            try:
                self._encoder = self._loader(self.config)
            except Exception as exc:
                self._error = str(exc)
                if isinstance(exc, EmbeddingUnavailable):
                    raise
                raise EmbeddingUnavailable(self._error) from exc
            return self._encoder

    def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        encoder = self._get_encoder()
        encoded = encoder.encode(list(texts), normalize_embeddings=self.config.normalize)
        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()
        if not isinstance(encoded, (list, tuple)) or len(encoded) != len(texts):
            raise RuntimeError("Embedding encoder returned an invalid batch")
        vectors = [_as_vector(item) for item in encoded]
        for vector in vectors:
            if len(vector) != self.config.dimension:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {self.config.dimension}, got {len(vector)}"
                )
        return [_normalize(vector) for vector in vectors] if self.config.normalize else vectors

    def embed_query(self, query: str) -> list[float]:
        value = query.strip()
        if not value:
            raise ValueError("Search query must not be empty")
        return self.embed_many([value])[0]

    def embed_documents(self, documents: Sequence[str]) -> list[list[float]]:
        if any(not value.strip() for value in documents):
            raise ValueError("Search documents must not be empty")
        return self.embed_many(documents)

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.config.enabled,
            "loaded": self._encoder is not None,
            "available": self.config.enabled and (self._encoder is not None or not self._load_attempted),
            "model": self.config.model_name,
            "revision": self.config.model_revision or None,
            "dimension": self.config.dimension,
            "device": self.config.device,
            "error": self._error,
        }


_service: EmbeddingService | None = None
_service_lock = RLock()


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = EmbeddingService()
    return _service


def set_embedding_service(service: EmbeddingService | None) -> None:
    """Override/reset the process singleton; primarily useful for tests."""
    global _service
    with _service_lock:
        _service = service
