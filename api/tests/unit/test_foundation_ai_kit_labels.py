"""A model sometimes answers a closed list with a label that is not on it (a specialty or kind the pack does
not have). One such label must not throw away a whole answer (a batch of pages): where the list allows
"cannot tell" (null) the label becomes null; otherwise the one list item that carries it is left out. Every
other kind of mismatch is still a wrong answer, and nothing about the label is logged but where it was."""

from __future__ import annotations

import copy
import logging
from typing import Any

import jsonschema_rs
import pytest

from app.core.ai.bedrock import ModelResponse
from app.core.ai.kit import AiKit, AiTask, InvalidOutput, mend_labels
from app.core.packs import default_library
from tests.support.ai import ScriptedBedrock, cell
from tests.support.packs import BOTH

pytestmark = pytest.mark.anyio

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page_no", "source_type", "evidence"],
                "properties": {
                    "page_no": {"type": "integer"},
                    "source_type": {"enum": ["letter", "result", None]},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["kind", "specialty"],
                            "properties": {
                                "kind": {"enum": ["received", "filed"]},
                                "specialty": {"enum": ["cardiology", None]},
                            },
                        },
                    },
                },
            },
        }
    },
}
VALIDATOR = jsonschema_rs.validator_for(SCHEMA)


def page(**overrides: Any) -> dict[str, Any]:
    return {"page_no": 1, "source_type": "letter", "evidence": [], **overrides}


def test_a_label_off_a_list_that_allows_null_becomes_null() -> None:
    mended, places = mend_labels({"pages": [page(source_type="ward_round")]}, VALIDATOR)
    assert mended == {"pages": [page(source_type=None)]}
    assert len(places) == 1
    assert VALIDATOR.is_valid(mended)


def test_a_label_off_a_list_without_null_leaves_out_only_the_item_carrying_it() -> None:
    evidence = [
        {"kind": "received", "specialty": None},
        {"kind": "sent", "specialty": None},
        {"kind": "filed", "specialty": "cardiology"},
    ]
    mended, places = mend_labels({"pages": [page(evidence=evidence)]}, VALIDATOR)
    assert mended["pages"][0]["evidence"] == [evidence[0], evidence[2]]
    assert len(places) == 1
    assert VALIDATOR.is_valid(mended)


def test_several_off_list_labels_across_pages_are_all_mended() -> None:
    answer = {
        "pages": [
            page(
                page_no=1, evidence=[{"kind": "sent", "specialty": "haematology"}, {"kind": "filed", "specialty": None}]
            ),
            page(page_no=2, source_type="ward_round", evidence=[{"kind": "received", "specialty": "haematology"}]),
        ]
    }
    mended, places = mend_labels(answer, VALIDATOR)
    assert mended == {
        "pages": [
            page(page_no=1, evidence=[{"kind": "filed", "specialty": None}]),
            page(page_no=2, source_type=None, evidence=[{"kind": "received", "specialty": None}]),
        ]
    }
    assert sorted(places) == sorted(
        [
            "pages.*.evidence.*.kind",
            "pages.*.evidence.*.specialty",
            "pages.*.source_type",
            "pages.*.evidence.*.specialty",
        ]
    )
    assert VALIDATOR.is_valid(mended)


def test_the_answer_given_is_never_changed_in_place() -> None:
    answer = {"pages": [page(source_type="ward_round")]}
    before = copy.deepcopy(answer)
    mend_labels(answer, VALIDATOR)
    assert answer == before


def test_any_other_mismatch_is_left_for_the_schema_check_to_reject() -> None:
    wrong: dict[str, Any]
    for wrong in ({"pages": "[{}]"}, {"pages": [{"page_no": 1}]}, {"pages": [page(page_no="one")]}, {"pages": []}):
        mended, places = mend_labels(wrong, VALIDATOR)
        assert (mended, places) == (wrong, [])
        assert not VALIDATOR.is_valid(mended)


def test_an_off_list_label_with_no_list_item_below_the_outermost_list_is_not_mended() -> None:
    schema = {
        "type": "object",
        "properties": {
            "kind": {"enum": ["a"]},
            "pages": {"type": "array", "items": {"type": "object", "properties": {"kind": {"enum": ["a"]}}}},
        },
    }
    validator = jsonschema_rs.validator_for(schema)
    assert mend_labels({"kind": "b"}, validator) == ({"kind": "b"}, [])
    assert mend_labels({"pages": [{"kind": "b"}]}, validator) == ({"pages": [{"kind": "b"}]}, [])  # never a whole page


PACK = default_library().combine(*BOTH[0].codes)


def answered(output: dict[str, Any]) -> ModelResponse:
    return ModelResponse(output=output, input_tokens=5, output_tokens=2, model_id=None)


class _Conn:
    async def execute(self, _sql: str, _params: tuple[object, ...]) -> None:
        return None


async def run(*answers: dict[str, Any]) -> tuple[dict[str, Any], ScriptedBedrock]:
    bedrock = ScriptedBedrock([answered(a) for a in answers])
    kit = AiKit(cell(), bedrock)
    t = AiTask(task_id="T-test", prompt_version="v1", model="sonnet", system="s", schema=SCHEMA, max_tokens=4096)
    return await kit.run(_Conn(), t, pack=PACK, text="synthetic text"), bedrock  # type: ignore[arg-type]


async def test_the_kit_keeps_an_answer_whose_only_fault_is_an_off_list_label_without_asking_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai")
    output, bedrock = await run({"pages": [page(source_type="ward_round")]})
    assert output == {"pages": [page(source_type=None)]}
    assert len(bedrock.requests) == 1
    lines = [r.getMessage() for r in caplog.records if "off-list" in r.getMessage()]
    assert lines and "count=1" in lines[0] and "pages.*.source_type" in lines[0]
    assert "ward_round" not in caplog.text


async def test_the_kit_still_rejects_an_answer_with_any_other_fault() -> None:
    with pytest.raises(InvalidOutput):
        await run({"pages": "[{}]"}, {"pages": [{"page_no": 1}]})
