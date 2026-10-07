from __future__ import annotations

import math
import re
from functools import lru_cache

from .config import get_settings


def tokens(value: str) -> set[str]:
    return {item for item in re.findall(r"\w+", value.casefold(), flags=re.UNICODE) if len(item) > 1}


def lexical_score(query: str, text: str) -> float:
    query_tokens = tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & tokens(text)) / len(query_tokens)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


@lru_cache(maxsize=1)
def embedding_model():
    settings = get_settings()
    if not settings.embedding_enabled:
        return None
    from sentence_transformers import SentenceTransformer
    path = settings.embedding_model_path or settings.embedding_model
    try:
        return SentenceTransformer(path, trust_remote_code=False)
    except Exception:
        return None


@lru_cache(maxsize=1)
def reranker_model():
    settings = get_settings()
    if not settings.reranker_enabled:
        return None
    from sentence_transformers import CrossEncoder
    path = settings.reranker_model_path or settings.reranker_model
    try:
        return CrossEncoder(path, trust_remote_code=False)
    except Exception:
        return None


def embed(values: list[str]) -> list[list[float]]:
    model = embedding_model()
    if model is None:
        return [[] for _ in values]
    encoded = model.encode(values, normalize_embeddings=True, show_progress_bar=False)
    return [[float(number) for number in vector] for vector in encoded]
