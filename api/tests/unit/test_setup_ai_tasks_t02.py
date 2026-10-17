"""T-02's output schema comes from the pack's report sections plus `other`, and holds no prose (rules 1, 11)."""

from __future__ import annotations

import pytest

from app.core.packs import load_library
from app.features.setup import ai_tasks
from tests.support.packs import FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo


@pytest.mark.parametrize("packs", [LAUNCH, FIXTURE], ids=["launch", "fixture"])
def test_the_sections_are_the_packs_plus_other(packs: Combo) -> None:
    pack = load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*packs.codes)
    task = ai_tasks.t02(pack)
    item = task.schema["properties"]["suggestions"]["items"]
    sections = item["properties"]["section"]["enum"]
    assert sections == [s["key"] for s in pack.report_sections()] + ["other"]
    assert set(item["properties"]) == {"index", "section", "reason"}
    assert item["properties"]["reason"]["maxLength"] <= 80
    assert task.task_id == "T-02" and task.prompt_version


def test_the_model_sees_each_heading_on_its_own_line_with_its_index() -> None:
    headings = [
        {"index": 1, "text": "Introduction", "style": "Heading 1"},
        {"index": 4, "text": "Opinion", "style": "Heading 1"},
    ]
    assert ai_tasks.t02_text(headings) == "1\tIntroduction\n4\tOpinion"
