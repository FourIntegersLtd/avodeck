"""TEMPORARY, dummy data only (ADR 0036): the direct API first, Bedrock when the direct call fails.

The kit keeps resolving Bedrock IDs; `FallbackClient` swaps in the role's direct ID for the primary and
sends the backup the original request. Only the three transport-level failures fall back: a wrong answer
(`InvalidOutput`, schema errors) is the kit's to handle and never hides behind the other provider.
"""

from __future__ import annotations

import logging

import pytest

from app.core.ai.bedrock import BedrockClient, ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import ModelRequestRefused
from app.core.ai.fallback import FallbackClient
from app.core.ai.kit import InvalidOutput
from app.core.config import ai_settings
from tests.support.ai import CELL_ENV, ScriptedBedrock, cell_env, response

pytestmark = pytest.mark.anyio

SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
OPUS = CELL_ENV["BEDROCK_OPUS_MODEL_ID"]
DIRECT = "claude-haiku-5-5"
SETTINGS = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))
URL = "https://api.anthropic.com"


def req(model_id: str = SONNET) -> ModelRequest:
    return ModelRequest(model_id=model_id, system="s", context={}, text="t", schema={"type": "object"})


def direct(*script: ModelResponse | Exception) -> ScriptedBedrock:
    return ScriptedBedrock(list(script), endpoint=URL)


def answered(model_id: str | None) -> ModelResponse:
    return ModelResponse(output={"ok": True}, input_tokens=5, output_tokens=2, model_id=model_id)


def test_it_is_a_model_client_on_the_primarys_endpoint() -> None:
    client = FallbackClient(SETTINGS, direct(answered(DIRECT)), None)
    assert isinstance(client, BedrockClient)
    assert client.endpoint == URL


async def test_the_primary_gets_the_direct_id_and_its_answer_names_the_model_that_gave_it() -> None:
    primary, backup = direct(answered(DIRECT)), ScriptedBedrock([response({})])
    got = await FallbackClient(SETTINGS, primary, backup).invoke(req())
    assert got.model_id == DIRECT
    assert [r.model_id for r in primary.requests] == [DIRECT]
    assert backup.requests == []


@pytest.mark.parametrize("failure", [ServiceBusy(3.0), ModelUnavailable("gone"), ModelRequestRefused("ValidationException")])
async def test_each_of_the_three_failures_falls_back_with_the_original_request(failure: Exception) -> None:
    primary, backup = direct(failure), ScriptedBedrock([answered(None)])
    original = req()
    got = await FallbackClient(SETTINGS, primary, backup).invoke(original)
    assert backup.requests == [original], "the backup sees the Bedrock request untouched"
    assert got.model_id in (None, SONNET), "the answer never claims to come from the direct model"
    assert got.output == {"ok": True}


async def test_a_fallback_logs_the_exception_type_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    primary = direct(ServiceBusy(3.0))
    await FallbackClient(SETTINGS, primary, ScriptedBedrock([answered(None)])).invoke(req())
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warned and "ServiceBusy" in warned[0].getMessage()


@pytest.mark.parametrize("wrong", [InvalidOutput("T-test"), ValueError("schema")])
async def test_a_wrong_answer_is_not_a_reason_to_fall_back(wrong: Exception) -> None:
    primary, backup = direct(wrong), ScriptedBedrock([answered(None)])
    with pytest.raises(type(wrong)):
        await FallbackClient(SETTINGS, primary, backup).invoke(req())
    assert backup.requests == []


async def test_with_no_backup_the_primarys_failure_propagates() -> None:
    with pytest.raises(ServiceBusy) as caught:
        await FallbackClient(SETTINGS, direct(ServiceBusy(9.0)), None).invoke(req())
    assert caught.value.retry_after == 9.0


async def test_if_the_backup_fails_too_its_exception_propagates_for_the_kits_backoff() -> None:
    primary, backup = direct(ModelUnavailable("gone")), ScriptedBedrock([ServiceBusy(4.0)])
    with pytest.raises(ServiceBusy) as caught:
        await FallbackClient(SETTINGS, primary, backup).invoke(req())
    assert caught.value.retry_after == 4.0


async def test_a_role_with_no_direct_id_goes_straight_to_the_backup() -> None:
    primary, backup = direct(answered(DIRECT)), ScriptedBedrock([answered(None)])
    await FallbackClient(SETTINGS, primary, backup).invoke(req(OPUS))
    assert primary.requests == []
    assert [r.model_id for r in backup.requests] == [OPUS]


async def test_a_role_with_no_direct_id_and_no_backup_is_unavailable() -> None:
    with pytest.raises(ModelUnavailable):
        await FallbackClient(SETTINGS, direct(answered(DIRECT)), None).invoke(req(OPUS))


async def test_a_request_for_an_unknown_model_goes_to_the_backup_to_refuse() -> None:
    primary, backup = direct(answered(DIRECT)), ScriptedBedrock([answered(None)])
    await FallbackClient(SETTINGS, primary, backup).invoke(req("anthropic.something-else-v1:0"))
    assert primary.requests == []


class _Conn:
    def __init__(self) -> None:
        self.params: tuple[object, ...] = ()

    async def execute(self, _sql: str, params: tuple[object, ...]) -> None:
        self.params = params


async def test_ai_calls_records_the_model_that_answered_and_tells_a_direct_call_apart() -> None:
    from types import SimpleNamespace

    from app.core.ai.kit import AiKit

    kit = AiKit(SETTINGS, object())  # type: ignore[arg-type]
    task = SimpleNamespace(task_id="T-test", prompt_version="v1")
    direct_conn, bedrock_conn = _Conn(), _Conn()
    await kit._log(direct_conn, task, SONNET, None, answered(DIRECT), "ok")  # type: ignore[arg-type]
    await kit._log(bedrock_conn, task, SONNET, None, answered(None), "ok")  # type: ignore[arg-type]
    assert direct_conn.params[2:4] == (DIRECT, "anthropic-direct")
    assert bedrock_conn.params[2:4] == (SONNET, SETTINGS.bedrock_region)
