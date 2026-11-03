"""TEMPORARY, dummy data only (ADR 0036): the kit tries the direct API first and falls back to Bedrock.

With `AI_PRIMARY_PROVIDER=anthropic` the kit gives a role with a direct model to the direct client. A busy
direct API (a 429 from its per-minute limits, or 529) is retried on the direct side up to three times,
honouring `retry-after` (capped at 30 s), so a burst never dumps onto Bedrock's small ceiling. Only after
that, or at once on an unavailable model or a refusal, does the same request go to Bedrock, under Bedrock's
own back-off. A request too big for Bedrock to answer raises `TooLargeForFallback`. A wrong answer is the
kit's own to handle and never hides behind the other provider.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.ai.bedrock import ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import ModelRequestRefused
from app.core.ai.cell import EndpointNotAllowed
from app.core.ai.kit import AiKit, AiTask, InvalidOutput, TooLargeForFallback
from app.core.errors import ProcessingPaused
from app.core.packs import default_library
from tests.support.ai import CELL_ENV, ScriptedBedrock, cell, cell_env, response
from tests.support.packs import BOTH

pytestmark = pytest.mark.anyio

SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
OPUS = CELL_ENV["BEDROCK_OPUS_MODEL_ID"]
DIRECT = "claude-haiku-5-5"
URL = "https://api.anthropic.com"
PACK = default_library().combine(*BOTH[0].codes)
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def settings() -> Any:
    from app.core.config import ai_settings

    return ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))


def task(model: str = "sonnet", max_tokens: int = 4096) -> AiTask:
    return AiTask(task_id="T-test", prompt_version="v1", model=model, system="s", schema=SCHEMA, max_tokens=max_tokens)


def answered(model_id: str | None = DIRECT, ok: bool = True) -> ModelResponse:
    return ModelResponse(output={"ok": ok}, input_tokens=5, output_tokens=2, model_id=model_id)


class _Conn:
    def __init__(self) -> None:
        self.logged: list[tuple[object, ...]] = []

    async def execute(self, _sql: str, params: tuple[object, ...]) -> None:
        self.logged.append(params)


class Rig:
    def __init__(
        self,
        direct: list[ModelResponse | Exception],
        bedrock: list[ModelResponse | Exception] | None = None,
        *,
        retries: int = 4,
    ) -> None:
        self.direct = ScriptedBedrock(direct, endpoint=URL)
        self.bedrock = ScriptedBedrock(bedrock if bedrock is not None else [answered(None)])
        self.sleeps: list[float] = []
        self.conn = _Conn()

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.kit = AiKit(settings(), self.bedrock, direct=self.direct, sleep=sleep, retries=retries)

    async def run(self, t: AiTask | None = None, **kwargs: Any) -> dict[str, Any]:
        return await self.kit.run(self.conn, t or task(), pack=PACK, text="synthetic text", **kwargs)  # type: ignore[arg-type]


async def test_the_direct_model_answers_and_bedrock_is_never_touched() -> None:
    rig = Rig([answered()])
    assert await rig.run() == {"ok": True}
    assert [r.model_id for r in rig.direct.requests] == [DIRECT]
    assert rig.bedrock.requests == []
    assert rig.conn.logged[0][2:4] == (DIRECT, "anthropic-direct"), "ai_calls names the model that answered"


async def test_a_busy_direct_api_is_retried_on_the_direct_side_honouring_retry_after() -> None:
    rig = Rig([ServiceBusy(7.0), ServiceBusy(11.0), answered()])
    assert await rig.run() == {"ok": True}
    assert rig.sleeps == [7.0, 11.0]
    assert len(rig.direct.requests) == 3 and rig.bedrock.requests == []


async def test_retry_after_is_capped_at_thirty_seconds() -> None:
    rig = Rig([ServiceBusy(120.0), answered()])
    await rig.run()
    assert rig.sleeps == [30.0]


async def test_without_a_retry_after_the_waits_grow_and_are_capped() -> None:
    rig = Rig([ServiceBusy(), ServiceBusy(), ServiceBusy(), answered()])
    await rig.run()
    assert rig.sleeps == [2.0, 4.0, 8.0]


async def test_after_three_retries_a_request_that_fits_goes_to_bedrock_with_its_own_model() -> None:
    rig = Rig([ServiceBusy(1.0)])
    assert await rig.run(task(max_tokens=8000)) == {"ok": True}
    assert len(rig.direct.requests) == 4, "the first call and three retries"
    assert rig.sleeps == [1.0, 1.0, 1.0]
    assert [r.model_id for r in rig.bedrock.requests] == [SONNET]
    assert rig.conn.logged[0][2:4] == (SONNET, cell().bedrock_region)


@pytest.mark.parametrize("failure", [ModelUnavailable("gone"), ModelRequestRefused("refusal")])
async def test_an_unavailable_model_or_a_refusal_goes_to_bedrock_at_once(failure: Exception) -> None:
    rig = Rig([failure])
    assert await rig.run() == {"ok": True}
    assert len(rig.direct.requests) == 1 and rig.sleeps == []
    assert [r.model_id for r in rig.bedrock.requests] == [SONNET]


async def test_a_fallback_logs_the_exception_type_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await Rig([ModelUnavailable("details that must not appear")]).run()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "ModelUnavailable" in text and "details that must not appear" not in text and "synthetic text" not in text


async def test_a_request_too_big_for_bedrock_raises_too_large_naming_its_page_limit() -> None:
    rig = Rig([ModelUnavailable("gone")])
    with pytest.raises(TooLargeForFallback) as caught:
        await rig.run(task(max_tokens=cell().limits["bedrock"].max_output_tokens + 1))
    assert caught.value.pages_per_call == cell().limits["bedrock"].pages_per_call == 10
    assert rig.bedrock.requests == []


async def test_a_request_exactly_at_bedrocks_largest_answer_still_falls_back() -> None:
    rig = Rig([ModelUnavailable("gone")])
    await rig.run(task(max_tokens=16000))
    assert len(rig.bedrock.requests) == 1


async def test_a_role_with_no_direct_model_goes_straight_to_bedrock() -> None:
    rig = Rig([answered()], [answered(None)])
    await rig.run(task("opus"))
    assert rig.direct.requests == [] and [r.model_id for r in rig.bedrock.requests] == [OPUS]


async def test_provider_bedrock_never_touches_the_direct_client() -> None:
    rig = Rig([answered()])
    assert await rig.run(provider="bedrock") == {"ok": True}
    assert rig.direct.requests == [] and len(rig.bedrock.requests) == 1


async def test_a_provider_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ValueError, match="provider"):
        await Rig([answered()]).run(provider="elsewhere")


async def test_bedrock_after_a_fallback_keeps_its_own_backoff_and_then_pauses() -> None:
    rig = Rig([ModelUnavailable("gone")], [ServiceBusy(5.0)], retries=2)
    with pytest.raises(ProcessingPaused) as caught:
        await rig.run()
    assert caught.value.reason == "service_busy"
    assert len(rig.bedrock.requests) == 3 and rig.sleeps[:1] == [5.0]


async def test_a_wrong_answer_is_asked_again_of_the_direct_model_and_never_falls_back() -> None:
    rig = Rig([ModelResponse(output={"ok": "no"}, input_tokens=1, output_tokens=1, model_id=DIRECT)])
    with pytest.raises(InvalidOutput):
        await rig.run()
    assert len(rig.direct.requests) == 2 and rig.bedrock.requests == []
    assert [c[-1] for c in rig.conn.logged] == ["invalid_output", "invalid_output"]


async def test_with_no_bedrock_a_gone_direct_model_pauses_processing() -> None:
    rig = Rig([ModelUnavailable("gone")])
    rig.kit.bedrock = None
    with pytest.raises(ProcessingPaused) as caught:
        await rig.run()
    assert caught.value.reason == "model_unavailable"


async def test_with_no_bedrock_a_still_busy_direct_api_pauses_after_its_retries() -> None:
    rig = Rig([ServiceBusy(1.0)])
    rig.kit.bedrock = None
    with pytest.raises(ProcessingPaused) as caught:
        await rig.run()
    assert caught.value.reason == "service_busy" and len(rig.direct.requests) == 4


async def test_with_no_bedrock_a_refusal_pauses_processing_as_model_unavailable() -> None:
    rig = Rig([ModelRequestRefused("refusal")])
    rig.kit.bedrock = None
    with pytest.raises(ProcessingPaused) as caught:
        await rig.run()
    assert caught.value.reason == "model_unavailable"


async def test_a_direct_endpoint_off_the_allow_list_is_refused_before_anything_is_sent() -> None:
    rig = Rig([answered()])
    rig.direct.endpoint = "https://elsewhere.example.com"
    with pytest.raises(EndpointNotAllowed):
        await rig.run()
    assert rig.direct.requests == []


async def test_a_bedrock_primary_kit_is_unchanged_and_has_no_direct_side() -> None:
    bedrock = ScriptedBedrock([ServiceBusy(2.0), response({"ok": True})])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    kit = AiKit(cell(), bedrock, sleep=sleep)
    out = await kit.run(_Conn(), task(), pack=PACK, text="synthetic text")  # type: ignore[arg-type]
    assert out == {"ok": True} and sleeps == [2.0] and len(bedrock.requests) == 2
    assert kit.direct is None and kit.primary_limits == cell().limits["bedrock"]


async def test_ai_calls_tells_a_direct_answer_apart_from_a_bedrock_one() -> None:
    kit = AiKit(settings(), object())  # type: ignore[arg-type]
    t = SimpleNamespace(task_id="T-test", prompt_version="v1")
    direct_conn, bedrock_conn = _Conn(), _Conn()
    await kit._log(direct_conn, t, SONNET, uuid.uuid4(), answered(DIRECT), "ok")  # type: ignore[arg-type]
    await kit._log(bedrock_conn, t, SONNET, uuid.uuid4(), answered(None), "ok")  # type: ignore[arg-type]
    assert direct_conn.logged[0][2:4] == (DIRECT, "anthropic-direct")
    assert bedrock_conn.logged[0][2:4] == (SONNET, settings().bedrock_region)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records]


async def test_a_fallback_line_names_the_class_status_reason_and_the_size_check(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    failure = ModelUnavailable("details that must not appear").annotated(
        status=400, error_type="invalid_request_error", error_code="enforced_spend_limit_reached", request_id="req_9"
    )
    await Rig([failure]).run(task(max_tokens=8000))
    (line,) = [m for m in _messages(caplog) if m.startswith("direct failed -> bedrock")]
    for expected in (
        "task=T-test",
        "reason=unavailable",
        "error_class=ModelUnavailable",
        "status=400",
        "error_code=enforced_spend_limit_reached",
        "request_id=req_9",
        "size_check=ok",
        "max_tokens=8000",
        "bedrock_max=16000",
    ):
        assert expected in line, expected
    assert "details that must not appear" not in "\n".join(_messages(caplog))


async def test_the_size_check_result_is_logged_when_the_request_is_too_big(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TooLargeForFallback):
        await Rig([ModelUnavailable("gone")]).run(task(max_tokens=32000))
    assert any("size_check=too_large" in m and "max_tokens=32000" in m for m in _messages(caplog))


async def test_a_fallback_after_busy_retries_logs_each_retry_and_says_busy_after_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    await Rig([ServiceBusy(1.0).annotated(status=429, error_type="rate_limit_error")]).run()
    messages = _messages(caplog)
    retries = [m for m in messages if m.startswith("direct retry:")]
    assert len(retries) == 3
    for n, line in zip((2, 3, 4), retries, strict=True):
        assert f"next_attempt={n}/4" in line and "retry_after=1.0" in line and "wait_s=1" in line
    assert any("reason=busy_after_retries" in m and "status=429" in m for m in messages)


async def test_the_client_is_told_which_attempt_it_is_making() -> None:
    from app.core.ai.bedrock import CALL_ATTEMPT

    seen: list[str | None] = []

    class Watching(ScriptedBedrock):
        async def invoke(self, request: Any) -> ModelResponse:
            seen.append(CALL_ATTEMPT.get())
            return await super().invoke(request)

    rig = Rig([answered()])
    watching = Watching([ServiceBusy(1.0), answered()], endpoint=URL)
    rig.kit.direct = watching
    await rig.run()
    assert seen == ["1/4", "2/4"]
    assert CALL_ATTEMPT.get() is None, "reset after the call"


async def test_a_rejected_answer_logs_who_gave_it_and_which_try(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig([ModelResponse(output={"ok": "no"}, input_tokens=1, output_tokens=1, model_id=DIRECT)])
    with pytest.raises(InvalidOutput):
        await rig.run()
    lines = [m for m in _messages(caplog) if m.startswith("model answer rejected")]
    assert len(lines) == 2
    assert "provider=anthropic" in lines[0] and f"model={DIRECT}" in lines[0] and "task=T-test" in lines[0]
    assert "outcome=invalid_output" in lines[0]
    assert "schema_attempt=1/2" in lines[0] and "schema_attempt=2/2" in lines[1]
    assert '"no"' not in "\n".join(_messages(caplog))
    # why it was rejected: the schema's keyword and path, the answer's size, never a key or value of the answer
    assert "out_tokens=1 max_tokens=" in lines[0] and "top_level_fields=1" in lines[0] and "errors=" in lines[0]
    assert "Required@" in lines[0] or "Type@" in lines[0] or "AdditionalProperties@" in lines[0]


async def test_a_quick_kit_keeps_the_direct_side_and_waits_out_no_retries() -> None:
    rig = Rig([answered()])
    rig.kit.bedrock = None
    quick = rig.kit.with_retries(0)
    assert quick.direct is rig.direct and quick.settings is rig.kit.settings and quick.retries == 0
    assert quick.direct_retries == 0
    assert await quick.run(rig.conn, task(), pack=PACK, text="synthetic text") == {"ok": True}  # type: ignore[arg-type]
    assert len(rig.direct.requests) == 1 and rig.sleeps == []


async def test_a_quick_kit_gives_up_on_a_busy_direct_api_at_once_instead_of_retrying() -> None:
    rig = Rig([ServiceBusy(5.0), answered()])
    rig.kit.bedrock = None
    quick = rig.kit.with_retries(0)
    with pytest.raises(ProcessingPaused) as caught:
        await quick.run(rig.conn, task(), pack=PACK, text="synthetic text")  # type: ignore[arg-type]
    assert caught.value.reason == "service_busy"
    assert len(rig.direct.requests) == 1 and rig.sleeps == []


async def test_a_quick_kit_with_bedrock_behind_it_falls_back_without_waiting() -> None:
    rig = Rig([ServiceBusy(5.0)])
    quick = rig.kit.with_retries(0)
    assert await quick.run(rig.conn, task(), pack=PACK, text="synthetic text") == {"ok": True}  # type: ignore[arg-type]
    assert len(rig.direct.requests) == 1 and len(rig.bedrock.requests) == 1 and rig.sleeps == []


async def test_a_kit_made_with_fewer_direct_retries_makes_only_those() -> None:
    rig = Rig([ServiceBusy(1.0)])
    rig.kit.direct_retries = 1
    await rig.run()
    assert len(rig.direct.requests) == 2 and rig.sleeps == [1.0]


async def test_a_wrong_answer_from_bedrock_after_a_fallback_is_asked_again_of_bedrock_not_the_direct_api() -> None:
    wrong = ModelResponse(output={"ok": "no"}, input_tokens=1, output_tokens=1)
    rig = Rig([ServiceBusy(1.0)], [wrong, answered(None)])
    assert await rig.run() == {"ok": True}
    assert len(rig.direct.requests) == 4, "the first call and three retries, and no more"
    assert len(rig.bedrock.requests) == 2
    assert [c[-1] for c in rig.conn.logged] == ["invalid_output", "ok"]
