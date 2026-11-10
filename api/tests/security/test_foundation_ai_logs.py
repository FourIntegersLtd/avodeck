"""AUDIT-14 E (rule 6): nothing from a document or a model answer reaches a log line, a raised error or an
`ai_calls` row.

Every path through the kit is run with the canary in the document text, in the model's answer values, in the
answer's KEYS and (for failures) in the provider's own message. The canary and the made-up name must then be in
no log record at DEBUG (message, arguments, attached traceback), in no raised exception (text, repr, arguments,
chained traceback) and in no row the kit wrote. Each scenario also asserts something that PROVES the path ran
(a log marker, the outcome row, the request that was sent), so a silent logger cannot pass.

The safe assertion is kept where the application is not safe, and the test is `xfail(strict=True)`. Synthetic
text only.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpx2
import jsonschema_rs
import pytest
from botocore.stub import Stubber

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import (
    ModelCallFailure,
    ModelRequestRefused,
    ModelUnavailable,
    ServiceBusy,
)
from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock
from app.core.ai.kit import AiKit, AiTask, InvalidOutput, OutputTruncated, mend_labels
from app.core.errors import ProcessingPaused
from tests.support.ai import CELL_ENV, NAME, ScriptedBedrock, cell, cell_env, response
from tests.support.ai_audit import PACK, VALID, FakeConn, strings, task
from tests.support.ai_injection import CANARY
from tests.support.live_ai import converse_response, sse_events

pytestmark = pytest.mark.anyio

DIRECT_HOST = "https://api.anthropic.com"
KEY = "sk-ant-ZXSENTINEL-logs-not-real"
SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
TEXT = f"Seen by {NAME} on the day. Note: {CANARY}. Labels follow."
CASE = uuid.UUID("00000000-0000-4000-8000-000000000014")
SECRETS = (CANARY, "ZXCANARY7741", NAME, "Pemberton", KEY)

MENDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page_no", "items"],
                "properties": {
                    "page_no": {"type": "integer"},
                    "tag": {"enum": ["x", "y", None]},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind"],
                            "properties": {"kind": {"enum": ["a", "b"]}},
                        },
                    },
                },
            },
        }
    },
}
DYNAMIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {"groups": {"type": "object", "additionalProperties": {"enum": ["a", None]}}},
}


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts: list[str] = []
    for record in caplog.records:
        parts += [record.getMessage(), repr(record.args), repr(record.msg), str(record.exc_text or "")]
        if record.exc_info:
            parts.append("".join(traceback.format_exception(*record.exc_info)))
    return "\n".join(parts)


def exception_text(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    return "\n".join([str(exc), repr(exc), repr(exc.args), "".join(traceback.format_exception(exc))])


def row_text(conn: FakeConn) -> str:
    return "\n".join(repr(row) + "\n" + "\n".join(strings(list(row))) for row in conn.logged)


async def no_sleep(_seconds: float) -> None:
    return None


def mending_answer(kind: str = CANARY, tag: str | None = "x") -> dict[str, Any]:
    return {"pages": [{"page_no": 1, "tag": tag, "items": [{"kind": "a"}, {"kind": kind}]}]}


@dataclass
class Scenario:
    """One path through the kit. `raises` is the exception it must end in (None: it returns)."""

    model: Any
    task: AiTask = field(default_factory=task)
    raises: type[BaseException] | None = None
    marker: str = ""  # text that must be in the logs, proving the path ran
    outcomes: list[str] | None = None  # the `ai_calls` outcomes written
    conn: FakeConn = field(default_factory=FakeConn)
    case_id: uuid.UUID | None = None
    direct: Any = None
    settings: Any = None
    sent: int | None = 1  # provider calls expected (None: do not check)
    returns: Any = None


def invalid(answer: dict[str, Any]) -> ScriptedBedrock:
    return ScriptedBedrock([response(answer), response(answer)])


def cut_off(tokens: int = 100) -> ScriptedBedrock:
    return ScriptedBedrock([response({"labels": CANARY}, output_tokens=tokens)])


def scenarios() -> dict[str, Callable[[], Scenario]]:
    def refused(text: str) -> Scenario:
        return Scenario(ScriptedBedrock([ModelRequestRefused(text)]), raises=ModelRequestRefused)

    return {
        "ok": lambda: Scenario(
            ScriptedBedrock([response({"labels": [CANARY]})]), outcomes=["ok"], returns={"labels": [CANARY]}
        ),
        "invalid_value": lambda: Scenario(
            invalid({"labels": CANARY}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "invalid_key": lambda: Scenario(
            invalid({CANARY: CANARY, "labels": ["a"]}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "invalid_long_item": lambda: Scenario(
            invalid({"labels": [CANARY * 4]}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "invalid_too_many": lambda: Scenario(
            invalid({"labels": [CANARY] * 9}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "invalid_nested_key": lambda: Scenario(
            invalid({"labels": [{CANARY: CANARY}]}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "mended_label": lambda: Scenario(
            ScriptedBedrock([response(mending_answer(CANARY, "x"))]),
            task=task(schema=MENDING_SCHEMA, mends_labels=True),
            marker="off-list labels count=1 at=pages.*.items.*.kind",
            outcomes=["ok"],
            returns={"pages": [{"page_no": 1, "tag": "x", "items": [{"kind": "a"}]}]},
        ),
        "mended_to_null": lambda: Scenario(
            ScriptedBedrock([response({"pages": [{"page_no": 1, "tag": CANARY, "items": []}]})]),
            task=task(schema=MENDING_SCHEMA, mends_labels=True),
            marker="off-list labels count=1 at=pages.*.tag",
            outcomes=["ok"],
            returns={"pages": [{"page_no": 1, "tag": None, "items": []}]},
        ),
        "not_mendable_so_invalid": lambda: Scenario(
            invalid({"pages": [{"page_no": CANARY, "items": [{"kind": CANARY}]}]}),
            task=task(schema=MENDING_SCHEMA, mends_labels=True),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=2,
        ),
        "cut_off": lambda: Scenario(
            cut_off(),
            task=task(max_tokens=100),
            raises=OutputTruncated,
            marker="was cut off",
            outcomes=["invalid_output"],
        ),
        "busy_always": lambda: Scenario(
            ScriptedBedrock([ServiceBusy(7.0)]),
            raises=ProcessingPaused,
            marker="still busy",
            outcomes=[],
            sent=5,
        ),
        "unavailable_with_echo": lambda: Scenario(
            ScriptedBedrock([ModelUnavailable(f"the model said {CANARY} {NAME}")]),
            raises=ProcessingPaused,
            marker="unavailable",
            outcomes=[],
        ),
        "refused_code_only": lambda: Scenario(
            ScriptedBedrock([ModelRequestRefused("ValidationException")]), raises=ModelRequestRefused
        ),
        "cost_guard": lambda: Scenario(
            ScriptedBedrock([response(VALID)]),
            conn=FakeConn(budget=(10**12, 0)),
            case_id=CASE,
            raises=ProcessingPaused,
            marker="cost_guard tripped",
            outcomes=[],
            sent=0,
        ),
        "direct_busy_then_bedrock": lambda: Scenario(
            ScriptedBedrock([response({"labels": [CANARY]})]),
            settings=cell_env_settings(),
            direct=ScriptedBedrock([ServiceBusy()], endpoint=DIRECT_HOST),
            marker="direct failed",
            outcomes=["ok"],
            returns={"labels": [CANARY]},
            sent=1,
        ),
        "direct_refused_with_echo_then_bedrock": lambda: Scenario(
            ScriptedBedrock([response({"labels": [CANARY]})]),
            settings=cell_env_settings(),
            direct=ScriptedBedrock([ModelRequestRefused(f"echo {CANARY} {NAME}")], endpoint=DIRECT_HOST),
            marker="direct failed",
            outcomes=["ok"],
            returns={"labels": [CANARY]},
            sent=1,
        ),
        "direct_unavailable_with_echo_then_bedrock": lambda: Scenario(
            ScriptedBedrock([response(VALID)]),
            settings=cell_env_settings(),
            direct=ScriptedBedrock([ModelUnavailable(f"echo {CANARY}")], endpoint=DIRECT_HOST),
            marker="direct failed",
            outcomes=["ok"],
            returns=VALID,
        ),
        "direct_invalid_twice": lambda: Scenario(
            ScriptedBedrock([response(VALID)]),
            settings=cell_env_settings(),
            direct=invalid_direct({"labels": CANARY}),
            raises=InvalidOutput,
            marker="outcome=invalid_output",
            outcomes=["invalid_output"] * 2,
            sent=0,
        ),
        # Recorded and flagged below: a typed failure that carries a message is passed up by the kit unchanged.
        "refused_with_echo": lambda: refused(f"echo {CANARY} {NAME}"),
    }


def cell_env_settings() -> Any:
    from app.core.config import ai_settings

    return ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY))


def invalid_direct(answer: dict[str, Any]) -> ScriptedBedrock:
    """Answers that name the direct model, as the live client's do, so the second ask stays with the direct API."""
    reply = replace(response(answer), model_id="claude-sonnet-4-6")
    return ScriptedBedrock([reply, reply], endpoint=DIRECT_HOST)


async def execute(scenario: Scenario, text: str = TEXT) -> BaseException | None:
    kit = AiKit(scenario.settings or cell(), scenario.model, direct=scenario.direct, sleep=no_sleep)
    try:
        out = await kit.run(
            scenario.conn,
            scenario.task,
            pack=PACK,
            text=text,
            case_id=scenario.case_id,
            names=(NAME,),
            subject="page:1",
        )
    except BaseException as exc:  # noqa: BLE001 - the audit inspects whatever the kit raises
        return exc
    if scenario.returns is not None:
        assert out == scenario.returns
    return None


SAFE = [name for name in scenarios() if name != "refused_with_echo"]


@pytest.mark.parametrize("name", SAFE)
async def test_E2_no_secret_reaches_a_log_a_raised_error_or_an_ai_calls_row(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    scenario = scenarios()[name]()
    raised = await execute(scenario)

    # The path really ran: the outcome, the marker, and what was sent.
    if scenario.raises is None:
        assert raised is None, f"{name}: {raised!r}"
    else:
        assert isinstance(raised, scenario.raises), f"{name}: {raised!r}"
    if scenario.outcomes is not None:
        assert scenario.conn.outcomes == scenario.outcomes
    logs = log_text(caplog)
    if scenario.marker:
        assert scenario.marker in logs, f"{name}: no log line proves the path ran"
    if scenario.sent is not None:
        assert len(scenario.model.requests) == scenario.sent
    if scenario.sent:  # the document text was really sent: the canary was in play, stripped only of the name
        assert any(CANARY in sent.text and NAME not in sent.text for sent in scenario.model.requests)
    assert any(record.name.startswith("app.ai") for record in caplog.records) or name in {"ok", "refused_code_only"}

    for secret in SECRETS:
        assert secret not in logs, f"{name}: {secret!r} in a log line"
        assert secret not in exception_text(raised), f"{name}: {secret!r} in a raised error"
        assert secret not in row_text(scenario.conn), f"{name}: {secret!r} in an ai_calls row"


@pytest.mark.parametrize("name", ["ok", "invalid_key", "mended_label"])
async def test_E2b_the_same_holds_for_a_case_run_with_a_case_id_and_a_subject(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    scenario = scenarios()[name]()
    scenario.case_id = CASE
    raised = await execute(scenario)
    assert (raised is None) == (scenario.raises is None)
    assert any("ai_budget" in sql for sql in scenario.conn.sql)  # control: the guard ran as well
    assert all(row[0] == CASE for row in scenario.conn.logged) and scenario.conn.logged
    for secret in SECRETS:
        assert secret not in log_text(caplog) + exception_text(raised) + row_text(scenario.conn)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-14 E-LOG1: the kit passes a typed ModelRequestRefused up with whatever message the client gave "
    "it (the contract says codes only, and the two live clients keep to it, but nothing enforces it), so a client "
    "or stand-in that echoes the input puts it in the raised text that job error handling and Sentry see: "
    "app/core/ai/kit.py:_invoke_bedrock (no except for ModelRequestRefused), app/core/ai/bedrock.py:112",
)
async def test_E2c_a_refusal_carrying_a_message_does_not_reach_the_raised_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    raised = await execute(scenarios()["refused_with_echo"]())
    assert isinstance(raised, ModelCallFailure)  # control: it did fail, and with the typed class
    assert CANARY not in exception_text(raised) + log_text(caplog)


# --- Answer keys and paths ---------------------------------------------------------------------------------------


async def test_E3_a_rejected_answer_is_logged_by_schema_path_never_by_the_models_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["groups"],
        "properties": {"groups": {"type": "object", "additionalProperties": {"type": "integer"}}},
    }  # a map: the model chooses the keys
    answer = {"groups": {CANARY: "not an integer", f"{CANARY}-2": 3}}
    kit = AiKit(cell(), ScriptedBedrock([response(answer)]), sleep=no_sleep)
    with pytest.raises(InvalidOutput) as caught:
        await kit.run(FakeConn(), task(schema=schema), pack=PACK, text=TEXT)
    logs = log_text(caplog)
    assert "outcome=invalid_output" in logs and "errors=Type:" in logs  # control: the kit gave the schema's reason
    assert CANARY not in logs + exception_text(caught.value)


async def test_E3b_the_batch_answer_size_is_logged_as_a_count_and_not_as_keys(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    answer = {f"{CANARY}-{i}": i for i in range(7)}
    kit = AiKit(cell(), ScriptedBedrock([response(answer)] * 2), sleep=no_sleep)
    with pytest.raises(InvalidOutput):
        await kit.run(FakeConn(), task(), pack=PACK, text=TEXT)
    assert "top_level_fields=7" in log_text(caplog)
    assert CANARY not in log_text(caplog)


def test_E4_a_mended_label_is_reported_by_place_with_list_positions_starred() -> None:
    validator = jsonschema_rs.validator_for(MENDING_SCHEMA)
    mended, places = mend_labels(mending_answer(CANARY, "x"), validator)
    assert places == ["pages.*.items.*.kind"] and CANARY not in "".join(places)
    assert mended["pages"][0]["items"] == [{"kind": "a"}]  # control: the off-list item really was left out
    assert not mend_labels({"pages": [{"page_no": 1, "tag": "x", "items": []}]}, validator)[1]  # nothing to mend


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-14 E-LOG2: a mended label inside an object whose KEYS the model chooses is logged by instance path, "
    "and `_starred` stars list positions only, so the model's key is written to the warning line (the `at=` text). "
    "Latent: no shipped task schema has model-chosen keys (all objects are closed), but the first one that "
    "does (a map keyed by page, by name, by date) and sets mends_labels logs the key: "
    "app/core/ai/kit.py:135-137 (_starred), 469-474 (_checked)",
)
async def test_E5_a_mended_label_under_a_model_chosen_key_does_not_log_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = {"groups": {f"{CANARY}": "bad", "ok": "a"}}
    kit = AiKit(cell(), ScriptedBedrock([response(answer)]), sleep=no_sleep)
    out = await kit.run(FakeConn(), task(schema=DYNAMIC_SCHEMA, mends_labels=True), pack=PACK, text=TEXT)
    assert out["groups"][CANARY] is None  # control: the label was mended to null, so the path really was logged
    assert "off-list labels count=1" in log_text(caplog)
    assert CANARY not in log_text(caplog)


def test_E5b_every_shipped_output_schema_is_closed_so_no_model_key_can_reach_a_path() -> None:
    """Why E-LOG2 is only latent. A scan of the schema builders: nothing but the error envelope opens a map."""
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "app"
    open_maps: list[str] = []
    for path in sorted(app.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if ("additionalProperties" in line or "patternProperties" in line) and "False" not in line:
                open_maps.append(f"{path.relative_to(app)}:{number}")
    assert all(entry.split(":")[0] in {"core/errors.py", "core/ai/strict.py"} for entry in open_maps), (
        f"a schema with model-chosen keys now exists, so E-LOG2 is live: {open_maps}"
    )


# --- The live clients, end to end through the kit ----------------------------------------------------------------


def anthropic_reply(output: dict[str, Any]) -> bytes:
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "record_output", "input": output}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    return sse_events(message)


async def test_E6_the_direct_client_logs_neither_the_text_nor_the_answer_on_success_or_a_bad_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = cell_env_settings()
    seen: list[httpx2.Request] = []
    answers: list[dict[str, Any]] = [{"labels": [CANARY]}, {CANARY: CANARY}, {CANARY: CANARY}]

    def handle(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(
            200, content=anthropic_reply(answers[len(seen) - 1]), headers={"content-type": "text/event-stream"}
        )

    live = LiveAnthropic(settings, KEY, transport=httpx2.MockTransport(handle))
    kit = AiKit(settings, ScriptedBedrock([response(VALID)]), direct=live, sleep=no_sleep)
    conn = FakeConn()
    assert await kit.run(conn, task(), pack=PACK, text=TEXT, names=(NAME,)) == {"labels": [CANARY]}
    with pytest.raises(InvalidOutput) as caught:
        await kit.run(conn, task(), pack=PACK, text=TEXT, names=(NAME,))
    assert len(seen) == 3 and all(CANARY.encode() in r.content and NAME.encode() not in r.content for r in seen)
    logs = log_text(caplog)
    assert "direct call: provider=anthropic" in logs and "outcome=invalid_output" in logs  # control
    for secret in SECRETS:
        assert secret not in logs + exception_text(caught.value) + row_text(conn), secret


async def test_E7_the_bedrock_client_logs_neither_the_text_nor_the_answer_on_success_or_a_bad_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    live = LiveBedrock(cell(), AwsCredentials("AKIAZXSENTINELLOG0", "aws-secret-ZXSENTINELLOG"))
    kit = AiKit(cell(), live, sleep=no_sleep)
    conn = FakeConn()
    with Stubber(live.client) as stub:
        stub.add_response("converse", converse_response({"labels": [CANARY]}))
        stub.add_response("converse", converse_response({CANARY: CANARY}))
        stub.add_response("converse", converse_response({CANARY: CANARY}))
        assert await kit.run(conn, task(), pack=PACK, text=TEXT, names=(NAME,)) == {"labels": [CANARY]}
        with pytest.raises(InvalidOutput) as caught:
            await kit.run(conn, task(), pack=PACK, text=TEXT, names=(NAME,))
        stub.assert_no_pending_responses()
    logs = log_text(caplog)
    assert "outcome=invalid_output" in logs and conn.outcomes == ["ok", "invalid_output", "invalid_output"]
    for secret in (*SECRETS, "aws-secret-ZXSENTINELLOG", "AKIAZXSENTINELLOG0"):
        assert secret not in logs + exception_text(caught.value) + row_text(conn), secret


async def test_E8_a_provider_error_that_echoes_the_text_is_not_logged_by_the_kit_or_raised_through_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = cell_env_settings()
    echo = {"type": "error", "error": {"type": "overloaded_error", "message": f"{NAME} {CANARY} {KEY}"}}

    def handle(_req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(529, json=echo)

    live = LiveAnthropic(settings, KEY, transport=httpx2.MockTransport(handle))
    fallback = ScriptedBedrock([response({"labels": [CANARY]})])
    kit = AiKit(settings, fallback, direct=live, sleep=no_sleep)
    conn = FakeConn()
    assert await kit.run(conn, task(), pack=PACK, text=TEXT, names=(NAME,)) == {"labels": [CANARY]}
    logs = log_text(caplog)
    assert "direct failed -> bedrock" in logs and "status=529" in logs  # control: the failure was logged by type
    for secret in SECRETS:
        assert secret not in logs + row_text(conn), secret
