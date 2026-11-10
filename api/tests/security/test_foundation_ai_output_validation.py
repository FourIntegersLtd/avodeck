"""AUDIT-14 O: what the model answers is checked by the kit before anything uses it.

Each test states the SAFE behaviour; where the application is not safe, the safe assertion is kept and the test is
marked `xfail(strict=True)` with the finding id. Every refusal has a positive control (the same call with a
legitimate answer is returned). Synthetic text only.
"""

from __future__ import annotations

import json
import math
from typing import Any

import jsonschema_rs
import pytest

from app.core.ai import anthropic_live
from app.core.ai.bedrock import ModelRequest
from app.core.ai.kit import AiKit, AiTask, InvalidOutput, OutputTruncated
from app.core.ai.provenance import ProvenanceError, check, check_items, supported
from app.core.ai.strict import strict_schema
from app.features.pipeline import extract
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.ai_audit import PACK, PACK_LAUNCH, SCHEMA, VALID, FakeConn, all_tasks, task
from tests.support.ai_injection import CANARY, INJECTIONS

pytestmark = pytest.mark.anyio

LONG = "x" * (SCHEMA["properties"]["labels"]["items"]["maxLength"] + 1)


async def ask(script: list[Any], t: AiTask | None = None) -> tuple[ScriptedBedrock, FakeConn, Any]:
    model = ScriptedBedrock(script)
    conn = FakeConn()
    try:
        out: Any = await AiKit(cell(), model).run(conn, t or task(), pack=PACK, text="synthetic text")
    except Exception as exc:  # the caller inspects the type
        out = exc
    return model, conn, out


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({}, id="empty_object"),
        pytest.param({"labels": "a"}, id="string_where_list_belongs"),
        pytest.param({"labels": [1]}, id="number_item"),
        pytest.param({"labels": [None]}, id="null_item"),
        pytest.param({"labels": [["a"]]}, id="nested_list_item"),
        pytest.param({"labels": {"0": "a"}}, id="object_where_list_belongs"),
        pytest.param({"other": ["a"]}, id="missing_required_and_unexpected"),
        pytest.param({"labels": ["a"], "extra": 1}, id="unexpected_field"),
        pytest.param({"labels": [LONG]}, id="over_long_string"),
        pytest.param({"labels": ["a"] * 6}, id="too_many_items"),
        pytest.param({"labels": ["a"], "": CANARY}, id="empty_key"),
        pytest.param({"labels": ["a"], "__proto__": {"x": CANARY}}, id="proto_key"),
        pytest.param({"labels": ["a"] * 5000}, id="huge_list"),
        pytest.param({"labels": [f"{CANARY}-{'y' * 5000}"]}, id="huge_string"),
    ],
)
async def test_O1_a_malformed_answer_is_retried_once_then_refused_and_never_returned(bad: dict[str, Any]) -> None:
    model, conn, out = await ask([response(bad)])
    assert isinstance(out, InvalidOutput) and not isinstance(out, OutputTruncated)
    assert len(model.requests) == 2, "exactly one retry"
    assert conn.outcomes == ["invalid_output", "invalid_output"]
    # no content was stored: the row holds ids, counts and the outcome only
    assert CANARY not in json.dumps(conn.logged, default=str) and "x" * 40 not in json.dumps(conn.logged)
    # positive control: the same call with a legitimate answer is returned
    _, ok_conn, ok = await ask([response(VALID)])
    assert ok == VALID and ok_conn.outcomes == ["ok"]


async def test_O2_a_second_try_that_is_valid_is_returned_and_the_first_never_is() -> None:
    first = {"labels": ["a"], "extra": CANARY}
    second = {"labels": ["b"]}
    model, conn, out = await ask([response(first), response(second)])
    assert out == second and CANARY not in json.dumps(out)
    assert len(model.requests) == 2
    assert conn.outcomes == ["invalid_output", "ok"]


async def test_O3_never_more_than_two_asks_for_an_always_invalid_model() -> None:
    model, conn, out = await ask([response({"labels": 5})])
    assert isinstance(out, InvalidOutput)
    assert len(model.requests) == 2 and len(conn.logged) == 2


async def test_O4_an_answer_that_filled_the_output_cap_is_truncated_and_not_asked_again() -> None:
    t = task()
    model, conn, out = await ask([response({"labels": ["a"], "cut": 1}, output_tokens=t.max_tokens)], t)
    assert isinstance(out, OutputTruncated)
    assert len(model.requests) == 1 and conn.outcomes == ["invalid_output"]
    # positive control: the same size answer that is valid is returned
    _, _, ok = await ask([response(VALID, output_tokens=t.max_tokens)], t)
    assert ok == VALID


NUMBERS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "i"],
    "properties": {
        "n": {"type": "number", "minimum": 0, "maximum": 10},
        "i": {"type": "integer", "minimum": 1, "maximum": 99},
    },
}


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"n": -1, "i": 1}, id="below_minimum"),
        pytest.param({"n": 11, "i": 1}, id="above_maximum"),
        pytest.param({"n": math.nan, "i": 1}, id="nan"),
        pytest.param({"n": math.inf, "i": 1}, id="infinity"),
        pytest.param({"n": -math.inf, "i": 1}, id="minus_infinity"),
        pytest.param({"n": True, "i": 1}, id="bool_as_number"),
        pytest.param({"n": "5", "i": 1}, id="number_as_string"),
        pytest.param({"n": 5, "i": 1.5}, id="fraction_as_integer"),
        pytest.param({"n": 5, "i": 0}, id="integer_below_minimum"),
        pytest.param({"n": 5, "i": 10**30}, id="huge_integer"),
        pytest.param({"n": 5, "i": None}, id="null_number"),
    ],
)
async def test_O5_a_number_out_of_range_or_of_the_wrong_kind_is_refused(bad: dict[str, Any]) -> None:
    t = task(NUMBERS)
    _, conn, out = await ask([response(bad)], t)
    assert isinstance(out, InvalidOutput) and conn.outcomes == ["invalid_output", "invalid_output"]
    _, _, ok = await ask([response({"n": 5, "i": 3})], t)
    assert ok == {"n": 5, "i": 3}


ENUM_NULLABLE: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["labels"],
    "properties": {"labels": {"type": "array", "items": {"enum": ["a", "b", None]}}},
}
ENUM_BATCH: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["labels"],
                "properties": {"labels": {"type": "array", "items": {"enum": ["a", "b"]}}},
            },
        }
    },
}
OFF_LIST = f"off-{CANARY}"


async def test_O6_an_off_list_label_is_refused_when_labels_are_not_mended() -> None:
    t = task(ENUM_NULLABLE)
    _, conn, out = await ask([response({"labels": ["a", OFF_LIST]})], t)
    assert isinstance(out, InvalidOutput) and conn.outcomes == ["invalid_output", "invalid_output"]
    _, _, ok = await ask([response({"labels": ["a", None]})], t)
    assert ok == {"labels": ["a", None]}


async def test_O7_a_mended_label_never_carries_the_off_list_value_and_the_row_says_ok() -> None:
    t = task(ENUM_NULLABLE, mends_labels=True)
    _, conn, out = await ask([response({"labels": ["a", OFF_LIST]})], t)
    assert out == {"labels": ["a", None]} and CANARY not in json.dumps(out)
    assert conn.outcomes == ["ok"] and CANARY not in json.dumps(conn.logged, default=str)


async def test_O8_an_off_list_label_in_a_batch_drops_only_that_item_not_the_page() -> None:
    t = task(ENUM_BATCH, mends_labels=True)
    _, conn, out = await ask([response({"pages": [{"labels": ["a", OFF_LIST, "b"]}]})], t)
    assert out == {"pages": [{"labels": ["a", "b"]}]} and CANARY not in json.dumps(out)
    assert conn.outcomes == ["ok"]


async def test_O9_an_off_list_label_on_the_outermost_list_is_refused_even_when_mending() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["pages"],
        "properties": {"pages": {"type": "array", "items": {"enum": ["a", "b"]}}},
    }
    t = task(schema, mends_labels=True)
    model, conn, out = await ask([response({"pages": ["a", OFF_LIST]})], t)
    assert isinstance(out, InvalidOutput) and len(model.requests) == 2
    assert CANARY not in json.dumps(conn.logged, default=str)


async def test_O10_mending_does_not_hide_other_errors_in_the_same_answer() -> None:
    t = task(ENUM_BATCH, mends_labels=True)
    bad = {"pages": [{"labels": ["a", OFF_LIST], "extra": 1}]}
    _, conn, out = await ask([response(bad)], t)
    assert isinstance(out, InvalidOutput) and conn.outcomes == ["invalid_output", "invalid_output"]


def test_O11_strict_mode_drops_the_bounds_so_the_kits_own_check_is_the_only_enforcement() -> None:
    sent = strict_schema(SCHEMA)
    flat = json.dumps(sent)
    # premise: what the direct API is given has no length or item bound
    assert "maxLength" not in flat and "maxItems" not in flat
    # the live client sends exactly that, and the task's own schema is untouched
    params = anthropic_live._params(
        ModelRequest(model_id="claude-x", system="s", context={}, text="t", schema=SCHEMA, max_tokens=100)
    )
    assert "maxLength" not in json.dumps(params["tools"][0]["input_schema"])
    assert SCHEMA["properties"]["labels"]["maxItems"] == 5
    # so an answer that strict mode lets through is still refused by the kit
    validator = jsonschema_rs.validator_for(sent)
    answer = {"labels": [LONG] * 6}
    assert validator.is_valid(answer), "strict form accepts it: nothing else would stop it"
    assert not jsonschema_rs.validator_for(dict(SCHEMA)).is_valid(answer)


async def test_O11b_the_kit_refuses_what_strict_mode_cannot_bound() -> None:
    model, conn, out = await ask([response({"labels": [LONG] * 6})])
    assert isinstance(out, InvalidOutput) and len(model.requests) == 2


@pytest.mark.parametrize("depth", [1, 50, 100])
async def test_O12_a_nested_answer_is_refused_as_invalid_output(depth: int) -> None:
    deep: Any = []
    for _ in range(depth):
        deep = [deep]
    model, conn, out = await ask([response({"labels": deep})])
    assert isinstance(out, InvalidOutput)
    assert conn.outcomes == ["invalid_output", "invalid_output"]


@pytest.mark.parametrize("depth", [300, 500])
async def test_O12_the_kit_turns_a_very_deep_answer_into_invalid_output_with_rows_logged(depth: int) -> None:
    deep: Any = []
    for _ in range(depth):
        deep = [deep]
    model, conn, out = await ask([response({"labels": deep})])
    assert isinstance(out, InvalidOutput), type(out).__name__
    assert conn.outcomes == ["invalid_output", "invalid_output"]


# --- every task's schema is closed and bounded ---------------------------------------------------------------


KNOWN: dict[str, set[str]] = {
    # task id -> the gaps the scan finds today (each one is its own xfail below)
    "open_object": set(),
    "unbounded_array": set(),
    "unbounded_string": set(),
    "unbounded_number": set(),
}


def gaps(node: Any, path: str = "") -> dict[str, list[str]]:
    """Schema-aware walk: the keys under `properties` are field names, not keywords."""
    found: dict[str, list[str]] = {k: [] for k in KNOWN}

    def merge(more: dict[str, list[str]]) -> None:
        for name, paths in more.items():
            found[name].extend(paths)

    if isinstance(node, dict):
        ty = node.get("type")
        kinds = ty if isinstance(ty, list) else [ty]
        if "object" in kinds and node.get("additionalProperties") is not False:
            found["open_object"].append(path)
        if "string" in kinds and not ({"enum", "const", "maxLength"} & set(node)):
            found["unbounded_string"].append(path)
        if "array" in kinds and "maxItems" not in node:
            found["unbounded_array"].append(path)
        if {"integer", "number"} & set(kinds) and not ({"maximum", "exclusiveMaximum"} & set(node)):
            found["unbounded_number"].append(path)
        for key, value in node.items():
            if key in ("properties", "patternProperties", "$defs", "definitions") and isinstance(value, dict):
                for name, sub in value.items():
                    merge(gaps(sub, f"{path}/{key}/{name}"))
            elif key in ("enum", "const", "required", "examples"):
                continue
            else:
                merge(gaps(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            merge(gaps(value, f"{path}[{i}]"))
    return found


REASONS = {
    "open_object": "AUDIT-14 O13a (info): T-07b fact `fields` is a bare {'type': 'object'} in the base schema (closed"
    " in effect by the allOf if/then per kind, see the next test) and no guard scans every task:"
    " app/features/pipeline/extract.py:113",
    "unbounded_array": "AUDIT-14 O13b (low): T-07b arrays (facts, printed_dates, entries, word_ids ...) have no"
    " maxItems, so only the output token cap bounds them: app/features/pipeline/extract.py:85-170",
    "unbounded_string": "AUDIT-14 O13c (low): T-22a `word_ids` items have no maxLength (checked later against the"
    " words): app/features/trial/ai_tasks.py:42",
    "unbounded_number": "AUDIT-14 O13d (info): an integer with only a minimum (page_no, index, matched_page): "
    "app/features/pipeline/extract.py:155, missing.py:46, setup/ai_tasks.py:98",
}


def scan_params() -> list[Any]:
    params: list[Any] = []
    for t in all_tasks():
        for name, ids in KNOWN.items():
            marks = [pytest.mark.xfail(strict=True, reason=REASONS[name])] if t.task_id in ids else []
            params.append(pytest.param(t, name, id=f"{t.task_id}-{name}", marks=marks))
    return params


@pytest.mark.parametrize(("t", "name"), scan_params())
def test_O13_every_task_schema_is_closed_and_bounded(t: AiTask, name: str) -> None:
    assert dict(t.schema).get("type") == "object"  # positive control: it is a real schema that was scanned
    assert gaps(dict(t.schema))[name] == []


def test_O13e_the_set_of_tasks_scanned_is_every_task_the_app_builds() -> None:
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "app"
    built = set()
    for p in app.rglob("*.py"):
        built.update(re.findall(r'task_id="(T-[0-9a-z]+)"', p.read_text()))
    scanned = {t.task_id for t in all_tasks()}
    assert built - scanned == set(), "a task is built that this scan does not cover"


def minimal(node: Any) -> Any:
    ty = node.get("type")
    kinds = ty if isinstance(ty, list) else [ty]
    if "enum" in node:
        return node["enum"][0]
    if "object" in kinds:
        return {k: minimal(v) for k, v in node.get("properties", {}).items() if k in node.get("required", [])}
    if "array" in kinds:
        return [minimal(node["items"]) for _ in range(node.get("minItems", 0))]
    if "string" in kinds:
        return "x" * node.get("minLength", 1)
    if {"integer", "number"} & set(kinds):
        return node.get("minimum", 1)
    return {}


def t07b_answer(fields: dict[str, Any]) -> dict[str, Any]:
    schema = dict(extract.task_for(PACK_LAUNCH).schema)
    answer: dict[str, Any] = minimal(schema)
    page = answer["pages"][0]
    fact: dict[str, Any] = minimal(schema["properties"]["pages"]["items"]["properties"]["facts"]["items"])
    fact["fields"] = fields
    page["facts"] = [fact]
    return answer


def test_O14_t07b_fact_fields_are_closed_in_effect_by_the_per_kind_conditions() -> None:
    validator = jsonschema_rs.validator_for(dict(extract.task_for(PACK_LAUNCH).schema))
    assert validator.is_valid(t07b_answer({})), "positive control: a minimal fact with no fields is valid"
    assert not validator.is_valid(t07b_answer({"zz_unlisted": "x"}))
    assert not validator.is_valid(t07b_answer({"zz_unlisted": {"nested": [1, 2]}}))
    assert not validator.is_valid(t07b_answer({"zz_unlisted": INJECTIONS["fabricate_information"]}))


# --- provenance (rule 2) ----------------------------------------------------------------------------------------


def wmap(*page_words: str, prefix: str = "w") -> dict[str, str]:
    return {f"{prefix}{i}": w for i, w in enumerate(page_words, start=1)}


PAGE = wmap("Creatinine", "was", "120", "mg", "on", "04/03/2020", "and", "BP", "120", "/", "80")
IDS = list(PAGE)


def reason(value: str, ids: list[str], page: dict[str, str]) -> str | None:
    try:
        check(value, ids, page)
    except ProvenanceError as exc:
        assert CANARY not in str(exc) and value not in str(exc) if value else True
        return exc.reason
    return None


def test_R1_positive_controls_a_value_in_its_words_is_accepted() -> None:
    assert reason("creatinine was 120 mg", IDS[:4], PAGE) is None
    assert reason("120", ["w3"], PAGE) is None
    assert reason("120,", ["w3"], PAGE) is None
    assert supported("Creatinine", ["w1"], PAGE)


@pytest.mark.parametrize(
    ("value", "ids", "expected"),
    [
        ("warfarin", ["w1", "w2"], "not_in_words"),
        (INJECTIONS["fabricate_information"], ["w1", "w2"], "not_in_words"),
        ("creatinine", ["w99"], "unknown_word"),
        ("creatinine", ["w1", "p2w1"], "unknown_word"),
        ("creatinine 120", ["w1", "w3"], "not_contiguous"),
        ("120 creatinine", ["w3", "w1"], "not_contiguous"),
        ("creatinine", [], "no_words"),
        ("", ["w1"], "empty_value"),
        ("   ", ["w1"], "empty_value"),
        ("...", ["w1"], "empty_value"),
        ("", [], "empty_value"),
    ],
)
def test_R2_a_fabricated_unknown_split_or_empty_value_is_refused_with_a_reason_code(
    value: str, ids: list[str], expected: str
) -> None:
    assert reason(value, ids, PAGE) == expected


def test_R3_word_ids_from_another_page_cannot_support_a_value() -> None:
    other = wmap("Warfarin", "5", "mg", prefix="p2w")
    assert reason("warfarin", ["p2w1"], PAGE) == "unknown_word"  # this page's words do not hold that id
    assert reason("warfarin", ["w1"], PAGE) == "not_in_words"  # right word, wrong page
    assert reason("warfarin", ["p2w1"], other) is None  # positive control: the page that holds it accepts it
    accepted, rejected = check_items(
        [{"value": "warfarin", "word_ids": ["p2w1"]}, {"value": "creatinine", "word_ids": ["w1"]}], PAGE
    )
    assert [r.reason for r in rejected] == ["unknown_word"] and len(accepted) == 1


@pytest.mark.parametrize(
    ("value", "page_words"),
    [
        ("12", ["120"]),
        ("120", ["12"]),
        ("120", ["1200"]),
        ("5", ["50"]),
        ("5", ["0.5"]),
        ("5", ["5.5"]),
        ("1.5", ["15"]),
        ("15", ["1.5"]),
        ("1.5", ["1,5"]),
        ("<60", ["60"]),
        ("60", ["<60"]),
        (">60", ["<60"]),
        ("-5", ["5"]),
        ("5", ["-5"]),
        ("+5", ["5"]),
        ("45%", ["45"]),
        ("45", ["45%"]),
        ("1 2 0", ["120"]),
        ("12 0", ["120"]),
        ("3/4", ["4/3"]),
        ("03/04/2020", ["04/03/2020"]),
        ("1e3", ["1000"]),
        ("creat", ["creatinine"]),
        ("creatininewas", ["creatinine", "was"]),
        ("١٢٠", ["120"]),  # arabic-indic digits are not the same atoms
        ("120", ["١٢٠"]),
    ],
)
def test_R4_number_and_sign_tricks_are_refused(value: str, page_words: list[str]) -> None:
    page = wmap(*page_words)
    assert reason(value, list(page), page) is not None
    # positive control: each page's own words are accepted as they are written
    assert reason(" ".join(page_words), list(page), page) is None


@pytest.mark.parametrize(
    ("value", "page_words"),
    [
        ("１２０", ["120"]),  # full-width digits normalise to the same number (NFKC, intended)
        ("CREATININE", ["creatinine"]),
        ("1,000", ["1000"]),
        ("01", ["1"]),
        ("1.0", ["1"]),
        ("0.5", [".5"]),
        ("ﬁbrinogen", ["fibrinogen"]),
    ],
)
def test_R5_the_normalisations_the_rule_allows_are_accepted(value: str, page_words: list[str]) -> None:
    page = wmap(*page_words)
    assert reason(value, list(page), page) is None


@pytest.mark.parametrize(
    ("value", "page_words"),
    [
        ("сreatinine", ["creatinine"]),  # first letter is Cyrillic
        ("Creat​inine", ["creatinine"]),  # zero-width space inside the word
        ("Creat‍inine", ["creatinine"]),
        ("creati­nine", ["creatinine"]),  # soft hyphen
        ("créatinine", ["creatinine"]),
        ("creatinine", ["Creat​inine"]),
        ("1​2", ["12"]),
    ],
)
def test_R6_look_alike_and_invisible_characters_cannot_make_a_different_word_match(
    value: str, page_words: list[str]
) -> None:
    page = wmap(*page_words)
    assert reason(value, list(page), page) is not None


@pytest.mark.parametrize("junk", ["‮", "‭", "⁦", "\U000e0041", "\x00", "\x07"])
def test_R7_a_value_with_bidi_or_control_characters_is_refused(junk: str) -> None:
    assert reason("120", ["w3"], PAGE) is None  # positive control: the clean value is accepted
    assert reason(f"{junk}120", ["w3"], PAGE) is not None


@pytest.mark.parametrize(("value", "page_word"), [("12", "12,5"), ("5", "12,5"), ("3", "3/4"), ("4", "3/4")])
def test_R8_a_part_of_a_decimal_comma_or_slashed_number_is_not_the_number(value: str, page_word: str) -> None:
    page = wmap(page_word)
    assert reason(page_word, ["w1"], page) is None  # positive control: the whole word is accepted
    assert reason(value, ["w1"], page) is not None


@pytest.mark.parametrize(("value", "page_words"), [("-80", ["120", "-", "80"]), ("-2", ["1", "-", "2"])])
def test_R9_a_spaced_hyphen_range_does_not_give_a_negative_number(value: str, page_words: list[str]) -> None:
    page = wmap(*page_words)
    assert reason(" ".join(page_words), list(page), page) is None  # positive control
    assert reason(value, list(page), page) is not None


def test_R10_a_refusal_carries_a_reason_code_only() -> None:
    for value in (INJECTIONS["reveal_system_prompt"], CANARY):
        try:
            check(value, ["w1"], PAGE)
        except ProvenanceError as exc:
            assert str(exc) == f"value refused: {exc.reason}"
            assert CANARY not in repr(exc) and CANARY not in str(exc.args)
        else:
            raise AssertionError("expected a refusal")
