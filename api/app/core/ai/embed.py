"""Embeddings (architecture 6.1 M7): one interface, a deterministic local stand-in in stage 1.

The stand-in hashes words into `DIMENSIONS` buckets (signed feature hashing) and normalises, so
tests and the synthetic bundle need no model download. An embedder takes only `pii.Stripped`
text (rule 7); a plain string is refused. The cell config (`EMBEDDER`) chooses the embedder.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from app.core.ai.onnx import OnnxModel
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


BATCH = 32
# bge asks for this prefix on short search questions, not on the passages searched.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class OnnxEmbedder:
    """bge-small-en-v1.5 int8 in this process (ADR 0029). Loaded on first use. A text longer than
    512 tokens is cut to its first 512 (the first window keeps recall and is the simplest)."""

    model = "bge-small-en-v1.5-onnx-int8"
    dimensions = DIMENSIONS

    def __init__(self, models_dir: Path | str = "/opt/models") -> None:
        self._model = OnnxModel(Path(models_dir) / "bge-small-en-v1.5-int8")

    def embed(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
        return self._run([self._text(t) for t in texts])

    def embed_queries(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
        return self._run([QUERY_PREFIX + self._text(t) for t in texts])

    @staticmethod
    def _text(item: Stripped) -> str:
        if not isinstance(item, Stripped):
            raise TypeError("embed only text that has been through pii.strip")
        return item.text

    def _run(self, texts: list[str]) -> list[tuple[float, ...]]:
        out: list[tuple[float, ...]] = []
        for start in range(0, len(texts), BATCH):
            hidden = self._model.run(texts[start : start + BATCH])[:, 0, :]  # the [CLS] vector
            norms = np.linalg.norm(hidden, axis=1, keepdims=True)
            out.extend(tuple(float(v) for v in row) for row in hidden / np.where(norms == 0, 1.0, norms))
        return out


def embed_queries(embedder: Embedder, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
    """Questions to search with: the embedder's query form when it has one, else plain `embed`."""
    query_form = getattr(embedder, "embed_queries", None)
    return query_form(texts) if query_form else embedder.embed(texts)


async def embed_batches(
    embedder: Embedder, texts: Sequence[Stripped], *, queries: bool = False
) -> list[tuple[float, ...]]:
    """Embeds `texts` in batches of `BATCH`, each forward pass in a worker thread so the event loop
    keeps serving (ADR 0029). `queries=True` uses the query form when the embedder has one."""
    out: list[tuple[float, ...]] = []
    for start in range(0, len(texts), BATCH):
        chunk = texts[start : start + BATCH]
        out.extend(await asyncio.to_thread(embed_queries if queries else _plain, embedder, chunk))
    return out


def _plain(embedder: Embedder, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
    return embedder.embed(texts)


def embedder_from_env(env: Mapping[str, str] | None = None) -> Embedder:
    """Auto-detect: `onnx` when model files exist at MODELS_DIR, else `recorded`."""
    source = os.environ if env is None else env
    kind = source.get("EMBEDDER", "").strip()
    models_dir = source.get("MODELS_DIR", "/opt/models")
    if not kind:
        kind = "onnx" if Path(models_dir).joinpath("bge-small-en-v1.5-int8").is_dir() else "recorded"
    if kind == "onnx":
        return OnnxEmbedder(models_dir)
    if kind != "recorded":
        raise ConfigError("EMBEDDER must be 'recorded', 'onnx' or unset (auto-detect)")
    return RecordedEmbedder()
