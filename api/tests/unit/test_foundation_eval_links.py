"""The links evaluation (T-11): recall@10 of the facts offered for each question (ADR 0029)."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.ai.embed import OnnxEmbedder, RecordedEmbedder
from app.core.ai.pii import Stripped
from app.core.packs import REPO
from app.evals import links
from app.evals import run as eval_run
from app.features.understand.links import fact_wording

EXPECTED = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice" / "expected"
MODELS = Path(os.environ.get("MODELS_DIR", "/opt/models"))
needs_models = pytest.mark.skipif(
    not (MODELS / "bge-small-en-v1.5-int8" / "model_quantized.onnx").exists(), reason="model files not present"
)


def test_recall_at_k_counts_expected_facts_found_in_the_first_k() -> None:
    assert links.recall_at_k(["a", "b", "c", "d"], {"a", "d"}, 2) == 0.5
    assert links.recall_at_k(["a", "b", "c", "d"], {"a", "d"}, 4) == 1.0
    assert links.recall_at_k([], {"a"}, 10) == 0.0


def test_a_question_with_more_expected_facts_than_k_scores_one_when_all_k_are_expected() -> None:
    assert links.recall_at_k(list("abcdef"), set("abcdefgh"), 4) == 1.0


def test_every_expected_link_names_a_fact_the_eval_ranks() -> None:
    corpus = set(links.pool_facts(EXPECTED))
    questions = json.loads((EXPECTED / "links.json").read_text())["questions"]
    assert [q["number"] for q in questions] == list(range(1, 11))
    for question in questions:
        assert question["expected"]
        for fact in question["expected"]:
            assert (fact["page"], fact["kind"], fact["quote"]) in corpus


def test_the_eval_embeds_exactly_the_wording_t11_embeds() -> None:
    seen: list[str] = []

    class Spy(RecordedEmbedder):
        def embed(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
            seen.extend(t.text for t in texts)
            return super().embed(texts)

    links.evaluate(Spy(), EXPECTED)
    wanted: dict[tuple[int, str, str], str] = {}
    for fact in json.loads((EXPECTED / "facts.json").read_text()):
        wanted.setdefault((fact["page"], fact["kind"], fact["quote"]), fact_wording(fact["kind"], fact["value"]))
    names = json.loads((EXPECTED / "case.json").read_text())["case_names"]
    assert len(seen) >= len(wanted)
    compared = 0
    for got, expected in zip(seen, wanted.values(), strict=False):
        if not any(name in expected for name in names) and "14.3.69" not in expected:  # identifiers are stripped
            assert got == expected
            compared += 1
    assert compared > len(wanted) // 2


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


def test_missing_model_files_stop_the_command_with_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBEDDER", "onnx")
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    assert eval_run.main(["--task", "links"]) == eval_run.NEEDS_CLOUD
    assert "model files" in capsys.readouterr().err
    monkeypatch.setenv("EMBEDDER", "recorded")
    monkeypatch.setenv("RERANKER", "onnx")
    assert eval_run.main(["--task", "links"]) == eval_run.NEEDS_CLOUD


def test_make_eval_and_the_nightly_run_the_links_task_with_and_without_the_reranker() -> None:
    makefile = (REPO / "api" / "Makefile").read_text()
    assert makefile.count("--task links") >= 2
    assert "RERANKER=onnx" in makefile
    assert "eval-links" in (REPO / ".github" / "workflows" / "eval-nightly.yml").read_text()


@needs_models
def test_the_in_process_model_with_the_reranker_meets_the_floor() -> None:
    from app.core.ai.rerank import OnnxReranker

    score = links.evaluate(OnnxEmbedder(MODELS), EXPECTED, reranker=OnnxReranker(MODELS)).recall
    assert score >= links.FLOOR
