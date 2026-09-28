from __future__ import annotations

import pytest

from app.embedding_service import (
    EmbeddingRuntimeConfig,
    EmbeddingService,
    EmbeddingUnavailable,
)


class FakeEncoder:
    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts, *, normalize_embeddings=True):
        self.calls += 1
        return [[3.0, 4.0] for _ in texts]


def _config(**values):
    return EmbeddingRuntimeConfig(dimension=2, **values)


def test_fake_encoder_is_injected_and_vectors_are_normalized():
    encoder = FakeEncoder()
    service = EmbeddingService(_config(), encoder=encoder)

    assert service.embed_query("frontend React") == pytest.approx([0.6, 0.8])
    assert encoder.calls == 1
    assert service.status()["loaded"] is True


def test_lazy_loader_runs_only_once():
    encoder = FakeEncoder()
    loads = []

    def load(config):
        loads.append(config.model_name)
        return encoder

    service = EmbeddingService(_config(), loader=load)
    service.embed_query("one")
    service.embed_query("two")

    assert loads == ["BAAI/bge-m3"]


def test_disabled_service_has_explicit_fallback_signal():
    service = EmbeddingService(_config(enabled=False), encoder=FakeEncoder())
    with pytest.raises(EmbeddingUnavailable, match="disabled"):
        service.embed_query("query")


def test_failed_load_is_guarded_and_not_retried_per_request():
    calls = []

    def fail(_config):
        calls.append(True)
        raise EmbeddingUnavailable("model missing")

    service = EmbeddingService(_config(), loader=fail)
    for _ in range(2):
        with pytest.raises(EmbeddingUnavailable, match="model missing"):
            service.embed_query("query")

    assert len(calls) == 1
    assert service.status()["available"] is False


def test_dimension_mismatch_is_rejected():
    service = EmbeddingService(EmbeddingRuntimeConfig(dimension=3), encoder=FakeEncoder())
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        service.embed_query("query")
