"""Optional local-only multilingual cross-encoder reranker."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol, Sequence


class RerankerUnavailable(RuntimeError):
    pass


class PairScorer(Protocol):
    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]: ...


@dataclass(frozen=True)
class RerankerConfig:
    enabled: bool = False
    model_name: str = "BAAI/bge-reranker-v2-m3"
    model_path: str = "models/bge-reranker-v2-m3"
    model_revision: str = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    device: str = "cpu"
    maximum_length: int = 512
    batch_size: int = 8
    top_k: int = 20
    weight: float = 0.70

    @classmethod
    def from_env(cls) -> "RerankerConfig":
        enabled = os.getenv("RERANKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            enabled=enabled,
            model_name=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip(),
            model_path=os.getenv("RERANKER_MODEL_PATH", "models/bge-reranker-v2-m3").strip(),
            model_revision=os.getenv("RERANKER_MODEL_REVISION", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e").strip(),
            device=os.getenv("RERANKER_DEVICE", "cpu").strip() or "cpu",
            maximum_length=int(os.getenv("RERANKER_MAX_LENGTH", "512")),
            batch_size=int(os.getenv("RERANKER_BATCH_SIZE", "8")),
            top_k=int(os.getenv("RERANKER_TOP_K", "20")),
            weight=float(os.getenv("RERANKER_WEIGHT", "0.70")),
        )


class _TransformersPairScorer:
    def __init__(self, config: RerankerConfig) -> None:
        model_path = Path(config.model_path).expanduser()
        if not model_path.is_dir():
            raise RerankerUnavailable(f"Reranker model is not installed at {model_path}")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RerankerUnavailable("torch and transformers are required for local reranking") from exc
        self._torch = torch
        self._device = config.device
        self._maximum_length = config.maximum_length
        self._batch_size = config.batch_size
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(model_path), local_files_only=True
        ).to(config.device)
        self._model.eval()

    def score(self, pairs: Sequence[tuple[str, str]]) -> Sequence[float]:
        values: list[float] = []
        with self._torch.no_grad():
            for start in range(0, len(pairs), self._batch_size):
                batch = list(pairs[start:start + self._batch_size])
                inputs = self._tokenizer(
                    batch, padding=True, truncation=True, return_tensors="pt",
                    max_length=self._maximum_length,
                )
                inputs = {key: value.to(self._device) for key, value in inputs.items()}
                logits = self._model(**inputs, return_dict=True).logits.view(-1).float().cpu().tolist()
                values.extend(1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, float(value)))))
                              for value in logits)
        return values


class RerankerService:
    def __init__(
        self,
        config: RerankerConfig | None = None,
        *,
        scorer: PairScorer | None = None,
        loader: Callable[[RerankerConfig], PairScorer] | None = None,
    ) -> None:
        self.config = config or RerankerConfig.from_env()
        self._scorer = scorer
        self._loader = loader or _TransformersPairScorer
        self._lock = RLock()
        self._load_attempted = scorer is not None
        self._error: str | None = None

    def _get_scorer(self) -> PairScorer:
        if not self.config.enabled:
            raise RerankerUnavailable("Candidate reranker is disabled")
        if self._scorer is not None:
            return self._scorer
        with self._lock:
            if self._scorer is not None:
                return self._scorer
            if self._load_attempted:
                raise RerankerUnavailable(self._error or "Candidate reranker is unavailable")
            self._load_attempted = True
            try:
                self._scorer = self._loader(self.config)
            except Exception as exc:
                self._error = str(exc)
                if isinstance(exc, RerankerUnavailable):
                    raise
                raise RerankerUnavailable(self._error) from exc
            return self._scorer

    def score_pairs(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        scorer = self._get_scorer()
        values = [float(value) for value in scorer.score([(query, document) for document in documents])]
        if len(values) != len(documents):
            raise RuntimeError("Reranker returned an invalid score batch")
        return [min(1.0, max(0.0, value)) for value in values]

    def status(self) -> dict[str, object]:
        model_present = Path(self.config.model_path).expanduser().is_dir()
        return {
            "enabled": self.config.enabled,
            "loaded": self._scorer is not None,
            "available": self.config.enabled and (
                self._scorer is not None or (not self._load_attempted and model_present)
            ),
            "model_present": model_present,
            "model": self.config.model_name,
            "revision": self.config.model_revision,
            "device": self.config.device,
            "top_k": self.config.top_k,
            "weight": self.config.weight,
            "error": self._error,
        }


_service: RerankerService | None = None
_service_lock = RLock()


def get_reranker_service() -> RerankerService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RerankerService()
    return _service


def set_reranker_service(service: RerankerService | None) -> None:
    global _service
    _service = service
