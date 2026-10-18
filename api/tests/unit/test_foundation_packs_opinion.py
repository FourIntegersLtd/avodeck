"""Case-type opinion words (slice E): point sides, opinion views, loaded language and the range-note heading.

They live in the case type's `opinion.yaml`; `{clinician}`-style placeholders are filled from the
vocabulary, so the launch labels say the overlay's local title and the fixture labels say the fixture's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs, write

SIDES = ["supports_claim", "supports_clinician", "neutral"]


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


def filled(text: str, combo: Combo) -> str:
    return re.sub(r"\{([a-z_]+)\}", lambda m: combo.word(m.group(1)), text)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_point_sides_are_the_three_core_codes_with_filled_labels(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("case-type", "opinion.yaml")
    sides = library.combine(*combo.codes).point_sides()
    assert [s["code"] for s in sides] == SIDES
    assert [s["label"] for s in sides] == [filled(s["label"], combo) for s in raw["point_sides"]]
    assert all("{" not in s["label"] for s in sides)


def test_launch_labels_read_as_the_brief_says(library: packs.PackLibrary) -> None:
    pack = library.combine(*LAUNCH.codes)
    assert [s["label"] for s in pack.point_sides()] == ["Supports the claim", "Supports the GP", "Neutral"]
    assert [v["label"] for v in pack.opinion_views()][-1] == "Below the standard of a reasonable GP"
    assert pack.range_note_prompt() == "What other GPs might reasonably do"
    assert {"clearly negligent", "obviously", "grossly", "appalling", "inexcusable", "blatant"} <= set(
        pack.loaded_language()
    )


def test_the_fixture_words_differ_from_the_launch_words(library: packs.PackLibrary) -> None:
    launch, fixture = library.combine(*LAUNCH.codes), library.combine(*FIXTURE.codes)
    assert launch.point_sides() != fixture.point_sides()
    assert launch.opinion_views() != fixture.opinion_views()
    assert {v["code"] for v in launch.opinion_views()}.isdisjoint(v["code"] for v in fixture.opinion_views())
    assert not set(launch.loaded_language()) & set(fixture.loaded_language())
    assert launch.range_note_prompt() != fixture.range_note_prompt()
    assert "therapists" in fixture.range_note_prompt()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_views_and_loaded_words_come_from_the_pack(library: packs.PackLibrary, combo: Combo) -> None:
    raw, pack = combo.raw("case-type", "opinion.yaml"), library.combine(*combo.codes)
    assert [v["code"] for v in pack.opinion_views()] == [v["code"] for v in raw["opinion_views"]]
    assert all(set(v) == {"code", "label"} and v["label"] for v in pack.opinion_views())
    assert pack.loaded_language() == raw["loaded_language"]
    assert pack.range_note_prompt() == filled(raw["range_note_prompt"], combo)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_prompt_context_for_the_points_and_lint_tasks(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    sides = {"point_sides": pack.point_sides()}
    who = {"profession": pack.profession, "clinician": pack.term("clinician"), "clinicians": pack.term("clinicians")}
    for task in ("T-14a", "T-14b", "T-14c"):
        context = pack.prompt_context(task)
        assert context["language"] == pack.language
        assert {k: context[k] for k in who} == who
        assert ("point_sides" in context) == (task == "T-14a")
        if task == "T-14a":
            assert context["point_sides"] == sides["point_sides"]
        assert "fact_kinds" not in context and "record_hints" not in context
    lint = pack.prompt_context("T-15a")
    assert lint["outside_expertise_question_types"] == pack.outside_expertise_question_types()
    assert lint["specialty"] == library.pack("specialty", combo.specialty).meta["name"]
    assert lint["loaded_language"] == pack.loaded_language()
    assert {k: lint[k] for k in who} == who


# --- schema ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    return copy_packs(tmp_path)


def opinion(fixtures: Path) -> Path:
    return fixtures / "case-type/zz-case/opinion.yaml"


def broken(tree: tuple[Path, Path], old: str, new: str) -> None:
    _, fixtures = tree
    path = opinion(fixtures)
    write(path, path.read_text().replace(old, new))


def test_an_unknown_key_is_rejected(tree: tuple[Path, Path]) -> None:
    broken(tree, "loaded_language:", "extra_words: [a]\nloaded_language:")
    with pytest.raises(packs.PackError, match="opinion.yaml"):
        packs.load_library(tree[1])


def test_a_point_side_without_a_code_is_rejected(tree: tuple[Path, Path]) -> None:
    broken(tree, "code: neutral, ", "")
    with pytest.raises(packs.PackError, match="opinion.yaml"):
        packs.load_library(tree[1])


def test_an_opinion_view_without_a_label_is_rejected(tree: tuple[Path, Path]) -> None:
    text = opinion(tree[1]).read_text()
    first = next(line for line in text.splitlines() if "zz_view" in line)
    broken(tree, first, re.sub(r",\s*label:[^}]*", "", first))
    with pytest.raises(packs.PackError, match="opinion.yaml"):
        packs.load_library(tree[1])


def test_point_sides_must_be_exactly_the_three_core_codes(tree: tuple[Path, Path]) -> None:
    broken(tree, "code: neutral", "code: zz_side")
    with pytest.raises(packs.PackError, match="opinion.yaml"):
        packs.load_library(tree[1])


def test_a_fourth_point_side_is_rejected(tree: tuple[Path, Path]) -> None:
    broken(tree, "loaded_language:", "point_sides_extra: []\nloaded_language:")
    with pytest.raises(packs.PackError, match="opinion.yaml"):
        packs.load_library(tree[1])
