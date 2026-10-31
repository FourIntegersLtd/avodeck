"""The in-process embedder and reranker (ADR 0029). Tests that need the model files skip without them;
CI fetches them with `scripts/fetch_models.py` into a cached folder and sets MODELS_DIR."""

from __future__ import annotations

import math
import os
import uuid
from pathlib import Path

import pytest

from app.core.ai import pii
from app.core.ai.embed import (
    DIMENSIONS,
    OnnxEmbedder,
    RecordedEmbedder,
    embed_queries,
    embedder_from_env,
)
from app.core.ai.rerank import OnnxReranker, reranker_from_env
from app.core.config import ConfigError
from app.core.packs import default_library
from tests.support.packs import LAUNCH

MODELS = Path(os.environ.get("MODELS_DIR", "/opt/models"))
EMBEDDER_DIR = MODELS / "bge-small-en-v1.5-int8"
RERANKER_DIR = MODELS / "ms-marco-minilm-l6-int8"
needs_embedder = pytest.mark.skipif(
    not (EMBEDDER_DIR / "model_quantized.onnx").exists(), reason="embedder model files not present"
)
needs_reranker = pytest.mark.skipif(
    not (RERANKER_DIR / "model_quantized.onnx").exists(), reason="reranker model files not present"
)


def stripped(text: str) -> pii.Stripped:
    return pii.strip(text, pack=default_library().combine(*LAUNCH.codes))


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_raw_text_is_refused_before_any_model_is_loaded(tmp_path: Path) -> None:
    embedder = OnnxEmbedder(tmp_path)  # lazy: no files needed to build it
    with pytest.raises(TypeError):
        embedder.embed(["not stripped"])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        embed_queries(embedder, ["not stripped"])  # type: ignore[list-item]


def test_the_cell_config_builds_the_onnx_embedder_without_loading_it(tmp_path: Path) -> None:
    embedder = embedder_from_env({"EMBEDDER": "onnx", "MODELS_DIR": str(tmp_path)})
    assert isinstance(embedder, OnnxEmbedder)
    assert embedder.model == "bge-small-en-v1.5-onnx-int8"
    assert embedder.dimensions == DIMENSIONS == 384


def test_embed_queries_falls_back_to_embed_for_an_embedder_without_a_query_form() -> None:
    texts = [stripped("urgent colonoscopy")]
    assert embed_queries(RecordedEmbedder(), texts) == RecordedEmbedder().embed(texts)


def test_the_cell_config_builds_the_reranker_only_when_asked(tmp_path: Path) -> None:
    assert reranker_from_env({}) is None
    assert isinstance(reranker_from_env({"RERANKER": "onnx", "MODELS_DIR": str(tmp_path)}), OnnxReranker)
    with pytest.raises(ConfigError):
        reranker_from_env({"RERANKER": "somewhere-else"})


@needs_embedder
def test_vectors_are_384_dimensions_and_unit_length_in_batches_of_32() -> None:
    embedder = OnnxEmbedder(EMBEDDER_DIR.parent)
    vectors = embedder.embed([stripped(f"HbA1c result number {n} raised") for n in range(70)])
    assert len(vectors) == 70
    for vector in (vectors[0], vectors[31], vectors[32], vectors[69]):
        assert len(vector) == 384
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-4)


@needs_embedder
def test_a_text_gets_the_same_vector_whatever_batch_it_is_in() -> None:
    embedder = OnnxEmbedder(EMBEDDER_DIR.parent)
    alone = embedder.embed([stripped("HbA1c raised")])[0]
    mixed = embedder.embed([stripped("HbA1c raised"), stripped("a much longer sentence " * 20)])[0]
    assert cosine(alone, mixed) > 0.999


@needs_embedder
def test_hba1c_raised_is_nearer_glycated_haemoglobin_high_than_an_ankle_sprain() -> None:
    embedder = OnnxEmbedder(EMBEDDER_DIR.parent)
    [query] = embed_queries(embedder, [stripped("HbA1c raised")])
    near, far = embedder.embed([stripped("glycated haemoglobin high"), stripped("ankle sprain")])
    assert cosine(query, near) > cosine(query, far)


@needs_embedder
def test_a_long_page_is_cut_to_the_first_512_tokens_not_refused() -> None:
    embedder = OnnxEmbedder(EMBEDDER_DIR.parent)
    [vector] = embedder.embed([stripped("word " * 5000)])
    assert len(vector) == 384


@needs_reranker
def test_the_reranker_puts_the_relevant_fact_first() -> None:
    reranker = OnnxReranker(RERANKER_DIR.parent)
    ids = [uuid.uuid4() for _ in range(3)]
    ranked = [
        (ids[0], "ankle sprain after football"),
        (ids[1], "Haemoglobin 104 g/L, iron deficiency anaemia"),
        (ids[2], "repeat prescription issued"),
    ]
    assert reranker.rerank("Was the anaemia investigated?", ranked)[0] == ids[1]


@needs_reranker
def test_only_the_top_50_are_reranked_the_rest_keep_their_order_after_them() -> None:
    reranker = OnnxReranker(RERANKER_DIR.parent)
    ids = [uuid.uuid4() for _ in range(60)]
    ranked = [(fid, f"note number {n}") for n, fid in enumerate(ids)]
    out = reranker.rerank("note", ranked)
    assert sorted(out) == sorted(ids)
    assert out[50:] == ids[50:]


def test_the_reranker_with_nothing_to_rank_loads_nothing(tmp_path: Path) -> None:
    assert OnnxReranker(tmp_path).rerank("q", []) == []
