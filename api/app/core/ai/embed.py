"""Embeddings (architecture 6.1 M7): one interface, a deterministic local stand-in in stage 1.

The stand-in hashes words into `DIMENSIONS` buckets (signed feature hashing) and normalises, so
tests and the synthetic bundle need no model download. An embedder takes only `pii.Stripped`
text (rule 7); a plain string is refused. The cell config (`EMBEDDER`) chooses the embedder.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.core.ai.pii import Stripped
from app.core.config import ConfigError

DIMENSIONS = 384
_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]: ...


class RecordedEmbedder:
    model = "recorded-hash-384"
    dimensions = DIMENSIONS

    def embed(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
        out = []
        for item in texts:
            if not isinstance(item, Stripped):
                raise TypeError("embed only text that has been through pii.strip")
            out.append(self._vector(item.text))
        return out

    def _vector(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * DIMENSIONS
        for word in _WORD.findall(text.lower()) or ["\0empty"]:
            digest = hashlib.sha256(word.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % DIMENSIONS
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:  # every word cancelled out: fall back to a fixed unit vector
            vector, norm = [1.0] + [0.0] * (DIMENSIONS - 1), 1.0
        return tuple(v / norm for v in vector)


def embedder_from_env(env: Mapping[str, str] | None = None) -> Embedder:
    """`EMBEDDER=recorded`. The hosted model arrives with the cloud cell."""
    source = os.environ if env is None else env
    kind = source.get("EMBEDDER", "").strip()
    if kind != "recorded":
        raise ConfigError("EMBEDDER must be 'recorded' until a hosted embedder is configured")
    return RecordedEmbedder()
