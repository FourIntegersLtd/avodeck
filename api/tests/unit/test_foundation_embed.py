"""Embeddings (architecture 6.1 M7): one interface, a deterministic local stand-in in stage 1.

The stand-in hashes words into 384 dimensions, so tests and the synthetic bundle need no model
download. Embedders take only text that has been through `pii.strip` (rule 7).
"""

from __future__ import annotations

import math

import pytest

from app.core.ai import pii
from app.core.ai.embed import DIMENSIONS, Embedder, RecordedEmbedder, embedder_from_env
from app.core.config import ConfigError
from app.core.packs import default_library
from tests.support.packs import FIXTURE, LAUNCH


def stripped(text: str, codes: tuple[str, str, str] = LAUNCH.codes) -> pii.Stripped:
    return pii.strip(text, pack=default_library().combine(*codes))


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_stand_in_gives_normalised_384_dimension_vectors_the_same_every_time() -> None:
    embedder: Embedder = RecordedEmbedder()
    assert DIMENSIONS == embedder.dimensions == 384
    assert embedder.model
    texts = [stripped("Colonoscopy report requested"), stripped("Ferritin result filed")]
    first, again = embedder.embed(texts), RecordedEmbedder().embed(texts)
    assert first == again
    for vector in first:
        assert len(vector) == 384
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_similar_text_is_closer_than_unrelated_text() -> None:
    a, b, c = RecordedEmbedder().embed(
        [
            stripped("colonoscopy report requested urgently"),
            stripped("urgent colonoscopy report"),
            stripped("repeat prescription issued"),
        ]
    )
    assert cosine(a, b) > cosine(a, c)


def test_works_under_the_fixture_packs_too() -> None:
    [vector] = RecordedEmbedder().embed([stripped("session notes", FIXTURE.codes)])
    assert len(vector) == 384


def test_raw_text_is_refused_it_must_go_through_pii_strip_first() -> None:
    with pytest.raises(TypeError):
        RecordedEmbedder().embed(["not stripped"])  # type: ignore[list-item]


def test_empty_text_still_gives_a_unit_vector() -> None:
    [vector] = RecordedEmbedder().embed([stripped("")])
    assert len(vector) == 384
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_the_cell_config_chooses_the_embedder() -> None:
    assert isinstance(embedder_from_env({"EMBEDDER": "recorded"}), RecordedEmbedder)
    # Unset: auto-detect. No model files at MODELS_DIR, so the recorded embedder.
    assert isinstance(embedder_from_env({"MODELS_DIR": "/no/such/models"}), RecordedEmbedder)
    with pytest.raises(ConfigError):
        embedder_from_env({"EMBEDDER": "somewhere-else"})
