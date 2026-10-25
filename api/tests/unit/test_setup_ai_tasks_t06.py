"""T-06's output schema: a label the code replaces anyway never fails the whole reading (screen 06).

`short_title` and `para_ref` are labels the server does not keep as given: a title over 40 characters falls
back to the question's own first clause (`letter.short_title`) and the paragraph reference is read from the
letter's layout. So a model label a little over 40 characters, or a long paragraph reference, must not make
the output invalid (two invalid outputs fail the job: "We couldn't read the letter"). Labels stay short
(80 characters at most, rule 1).
"""

from __future__ import annotations

import jsonschema_rs
import pytest

from app.core.packs import load_library
from app.features.setup import ai_tasks
from app.features.setup.letter import short_title
from tests.support.packs import FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo

QUESTION = "Please comment on the standard of care given on 14 May, and whether it fell below that expected."


def output(kind: str, title: str, para_ref: str) -> dict[str, object]:
    item = {"text_verbatim": QUESTION, "word_ids": ["p1w1"], "para_ref": para_ref, "reason": "asks about care"}
    return {"questions": [{**item, "kind": kind, "short_title": title}], "assumptions": [item]}


@pytest.mark.parametrize("packs", [LAUNCH, FIXTURE], ids=["launch", "fixture"])
def test_a_long_label_or_paragraph_reference_is_still_a_valid_output(packs: Combo) -> None:
    pack = load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*packs.codes)
    task = ai_tasks.t06(pack)
    kind = task.schema["properties"]["questions"]["items"]["properties"]["kind"]["enum"][0]
    title = "Standard of care at the consultation in May"  # 43 characters
    assert jsonschema_rs.validator_for(dict(task.schema)).is_valid(output(kind, title, "Paragraph 4(a)(ii) and (iii)"))
    assert 0 < len(short_title(QUESTION, title)) <= 40


@pytest.mark.parametrize("packs", [LAUNCH, FIXTURE], ids=["launch", "fixture"])
def test_every_label_stays_short(packs: Combo) -> None:
    pack = load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*packs.codes)
    task = ai_tasks.t06(pack)
    kind = task.schema["properties"]["questions"]["items"]["properties"]["kind"]["enum"][0]
    validator = jsonschema_rs.validator_for(dict(task.schema))
    assert not validator.is_valid(output(kind, "x" * 81, "4(a)"))
    assert not validator.is_valid(output(kind, "Breach", "x" * 81))
