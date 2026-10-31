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
            tokenizer.enable_padding()
            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 2
            self._tokenizer = tokenizer
            self._session = onnxruntime.InferenceSession(
                str(self._folder / "model_quantized.onnx"), options, providers=["CPUExecutionProvider"]
            )

    def run(self, texts: list[str], pairs: list[str] | None = None) -> np.ndarray:
        """Model output for these texts (or text pairs), padded to the longest in the batch."""
        self._load()
        encoded = self._tokenizer.encode_batch(list(zip(texts, pairs, strict=True)) if pairs is not None else texts)
        feeds: dict[str, np.ndarray] = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
        }
        if "token_type_ids" in {i.name for i in self._session.get_inputs()}:
            feeds["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
        return np.asarray(self._session.run(None, feeds)[0])
