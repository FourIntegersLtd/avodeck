"""Shared plumbing for the in-process models (ADR 0029): an int8 ONNX file and its tokenizer.

Files are baked into the image (`/opt/models`, `scripts/fetch_models.py`); nothing is downloaded at
run time and no text leaves the worker. Models load on first use, once per process.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

MAX_TOKENS = 512
TOKEN_BUDGET = 4096  # rows x longest row per forward pass


class OnnxModel:
    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: Any = None

    def _load(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime  # type: ignore[import-untyped]
            from tokenizers import Tokenizer  # type: ignore[import-untyped]

            tokenizer = Tokenizer.from_file(str(self._folder / "tokenizer.json"))
            tokenizer.enable_truncation(MAX_TOKENS)  # a long page is cut to its first window
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            self._tokenizer = tokenizer
            self._session = onnxruntime.InferenceSession(
                str(self._folder / "model_quantized.onnx"), options, providers=["CPUExecutionProvider"]
            )

    def run(self, texts: list[str], pairs: list[str] | None = None) -> np.ndarray:
        """Model output for these texts (or text pairs). Long inputs go through in smaller slices so
        one call stays inside the worker's memory (attention grows with length squared)."""
        self._load()
        encoded = self._tokenizer.encode_batch(list(zip(texts, pairs, strict=True)) if pairs is not None else texts)
        parts, start = [], 0
        while start < len(encoded):
            widest = max(len(e.ids) for e in encoded[start : start + 32])
            size = max(1, min(32, TOKEN_BUDGET // widest))
            parts.append(self._forward(encoded[start : start + size]))
            start += size
        return _stack(parts)

    def _forward(self, encoded: list[Any]) -> np.ndarray:
        longest = max(len(e.ids) for e in encoded)

        def pad(rows: list[list[int]]) -> np.ndarray:
            return np.array([r + [0] * (longest - len(r)) for r in rows], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {
            "input_ids": pad([e.ids for e in encoded]),
            "attention_mask": pad([e.attention_mask for e in encoded]),
        }
        if "token_type_ids" in {i.name for i in self._session.get_inputs()}:
            feeds["token_type_ids"] = pad([e.type_ids for e in encoded])
        return np.asarray(self._session.run(None, feeds)[0])


def _stack(parts: list[np.ndarray]) -> np.ndarray:
    """Slices padded to different lengths: pad the token axis of 3-D outputs, then join."""
    if parts[0].ndim == 3:
        width = max(p.shape[1] for p in parts)
        parts = [np.pad(p, ((0, 0), (0, width - p.shape[1]), (0, 0))) for p in parts]
    return np.concatenate(parts)
