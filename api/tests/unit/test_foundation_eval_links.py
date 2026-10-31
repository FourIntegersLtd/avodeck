"""The links evaluation (T-11): recall@10 of the facts offered for each question (ADR 0029)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.ai.embed import OnnxEmbedder, RecordedEmbedder
from app.core.packs import REPO
from app.evals import links
from app.evals import run as eval_run

EXPECTED = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"
MODELS = Path(os.environ.get("MODELS_DIR", "/opt/models"))
needs_models = pytest.mark.skipif(
    not (MODELS / "bge-small-en-v1.5-int8" / "model_quantized.onnx").exists(), reason="model files not present"
)


def test_recall_at_k_counts_expected_facts_found_in_the_first_k() -> None:
    assert links.recall_at_k(["a", "b", "c", "d"], {"a", "d"}, 2) == 0.5
    assert links.recall_at_k(["a", "b", "c", "d"], {"a", "d"}, 4) == 1.0
    assert links.recall_at_k([], {"a"}, 10) == 0.0


def test_every_expected_link_names_a_fact_of_the_synthetic_bundle() -> None:
    corpus = {(f["page"], f["kind"], f["quote"]) for f in json.loads((EXPECTED / "facts.json").read_text())}
    questions = json.loads((EXPECTED / "links.json").read_text())["questions"]
    assert [q["number"] for q in questions] == [1, 2, 3, 4]
    for question in questions:
        assert question["expected"]
        for fact in question["expected"]:
            assert (fact["page"], fact["kind"], fact["quote"]) in corpus


def test_the_stand_in_scores_below_the_floor_so_the_eval_can_tell_the_models_apart() -> None:
    score = links.evaluate(RecordedEmbedder(), EXPECTED).recall
    assert 0.0 <= score < links.FLOOR


def test_the_command_fails_below_the_floor_and_prints_counts_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBEDDER", "recorded")
    assert eval_run.main(["--task", "links"]) == 1
    out = capsys.readouterr().out
    assert "links recall@10" in out and "floor" in out
    assert "Ashworth" not in out and "colon" not in out.lower()


@needs_models
def test_the_in_process_model_meets_the_floor() -> None:
    score = links.evaluate(OnnxEmbedder(MODELS), EXPECTED).recall
    assert score >= links.FLOOR
