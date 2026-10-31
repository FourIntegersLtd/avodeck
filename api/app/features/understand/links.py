"""Link candidates for T-11: the facts most like each question, best first, cut to k, then reranked.

Vectors come from `core/ai/embed.py` (identifier-stripped text). The reranker is a seam: the cell's
reranker when foundation provides one, else `IdentityReranker`, which keeps the embedding order.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from typing import Protocol

Vector = Sequence[float]
RERANK_POOL = 50


class Reranker(Protocol):
    def rerank(self, query: str, ranked: list[tuple[uuid.UUID, str]]) -> list[uuid.UUID]: ...


class IdentityReranker:
    def rerank(self, query: str, ranked: list[tuple[uuid.UUID, str]]) -> list[uuid.UUID]:
        return [fid for fid, _ in ranked]


def fact_wording(kind: str, value: str) -> str:
    """What is embedded for a fact. T-11 and the links evaluation both use this."""
    return f"{kind} {value}"


def _cosine(a: Vector, b: Vector) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def candidates(
    query: tuple[Vector, str],
    facts: Sequence[tuple[uuid.UUID, Vector, str]],
    *,
    k: int,
    reranker: Reranker,
) -> list[uuid.UUID]:
    vector, text = query
    wide = (
        k if isinstance(reranker, IdentityReranker) else max(k, RERANK_POOL)
    )  # a real reranker sees more than it keeps
    scored = sorted(facts, key=lambda f: -_cosine(vector, f[1]))[:wide]
    return reranker.rerank(text, [(fid, words) for fid, _, words in scored])[:k] if scored else []
