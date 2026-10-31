"""Reranker (T-11 link candidates): an in-process cross-encoder, `RERANKER=onnx` (ADR 0029).

It satisfies `features/understand/links.py` `Reranker`. Text arrives already identifier-stripped
(the caller strips it). Only the first `TOP` candidates are scored; the rest keep their order after them.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from pathlib import Path

from app.core.ai.onnx import OnnxModel
from app.core.config import ConfigError

TOP = 50
DEFAULT_MODELS_DIR = "/opt/models"
FOLDER = "ms-marco-minilm-l6-int8"


class OnnxReranker:
    def __init__(self, models_dir: Path | str = DEFAULT_MODELS_DIR) -> None:
        self._model = OnnxModel(Path(models_dir) / FOLDER)

    def rerank(self, query: str, ranked: list[tuple[uuid.UUID, str]]) -> list[uuid.UUID]:
        if not ranked:
            return []
        head, tail = ranked[:TOP], ranked[TOP:]
        scores = self._model.run([query] * len(head), [words for _, words in head]).reshape(len(head), -1)[:, 0]
        order = sorted(range(len(head)), key=lambda i: -float(scores[i]))  # stable: ties keep embedding order
        return [head[i][0] for i in order] + [fid for fid, _ in tail]


def reranker_from_env(env: Mapping[str, str] | None = None) -> OnnxReranker | None:
    """Auto-detect: `onnx` when model files exist at MODELS_DIR, else off (embedding order)."""
    source = os.environ if env is None else env
    kind = source.get("RERANKER", "").strip()
    models_dir = source.get("MODELS_DIR", DEFAULT_MODELS_DIR)
    if not kind:
        kind = "onnx" if Path(models_dir).joinpath("bge-reranker-base-int8").is_dir() else ""
    if not kind:
        return None
    if kind != "onnx":
        raise ConfigError("RERANKER must be 'onnx' or unset (auto-detect)")
    return OnnxReranker(models_dir)
