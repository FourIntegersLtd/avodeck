"""AUDIT-14 M: the model and provider side of the AI kit (rule 5 and rule 6).

Model IDs and endpoints are refused unless they are the cell's own; the temporary direct path (ADR 0036) is named
for what it is; credentials stay out of every repr, log line and exception; keys come only from the given mapping;
retries and timeouts are bounded; the cost guard stops a call before anything is sent; and provider error bodies
that echo the input never reach a log or a raised message.

Each test states the SAFE behaviour. Where the application is not safe, the safe assertion is kept and the test is
marked `xfail(strict=True)` with the finding id. Every "refused" has a positive control (the same call works when
legitimate). Everything is synthetic; the canary has no meaning.

Numbers recorded here (the audit report quotes them): SDK versions come from `poetry.lock` (anthropic 2.4.0 on
httpx2 2.16.0, boto3 and botocore 1.43.114). Timeouts on both clients: 10 s connect, 300 s read. SDK retries: none
on either (boto `total_max_attempts=1`, anthropic `max_retries=0`), the kit owns back-off.
"""

from __future__ import annotations

import ast
import contextlib
import logging
import traceback
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.stub import Stubber
from urllib3.util import parse_url

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import ModelCallFailure, ModelRequest, ModelRequestRefused, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock
from app.core.ai.cell import EndpointNotAllowed, ModelNotAllowed, check_endpoint, check_model
from app.core.ai.clients import live_or_recorded
from app.core.ai.kit import AiKit, AiTask, InvalidOutput, backoff_delays
from app.core.config import ConfigError, ai_settings, endpoint_allowed, in_region_model_id
from app.core.errors import ProcessingPaused
from app.core.logs import sentry_options
from tests.support.ai import CELL_ENV, NAME, ScriptedBedrock, cell, cell_env, response
from tests.support.ai_audit import PACK, SCHEMA, VALID, FakeConn, task
from tests.support.ai_injection import CANARY
from tests.support.live_ai import AWS_KEYS, converse_response, sse_events

pytestmark = pytest.mark.anyio

APP = Path(__file__).resolve().parents[2] / "app"
KEY = "sk-ant-ZXSENTINEL-not-real"
SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
OPUS = CELL_ENV["BEDROCK_OPUS_MODEL_ID"]
DIRECT = "claude-sonnet-4-6"
ON = {"AI_PRIMARY_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": KEY}
CREDS = AwsCredentials("AKIAZXSENTINELID000", "aws-secret-ZXSENTINEL", "aws-token-ZXSENTINEL")
BEDROCK_HOST = "bedrock-runtime.eu-west-2.amazonaws.com"
HOSTS = cell().allowed_hosts
INVALID = {"labels": "not a list"}  # valid JSON, fails the schema


def direct_settings() -> Any:
    return ai_settings(cell_env(**ON))


def request(model_id: str = SONNET, text: str = "line one\nline two") -> ModelRequest:
    return ModelRequest(
        model_id=model_id,
        system="Label each line.",
        context={},
        text=text,
        schema=SCHEMA,
        temperature=0.0,
        max_tokens=256,
        task_id="T-test",
        subject=None,
    )


async def no_sleep(_seconds: float) -> None:
    return None


class Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Every part of every record a handler could print: message, args, and any attached traceback."""
    parts: list[str] = []
    for record in caplog.records:
        parts += [record.getMessage(), repr(record.args), str(record.exc_text or "")]
        if record.exc_info:
            parts.append("".join(traceback.format_exception(*record.exc_info)))
    return "\n".join(parts)


def exception_text(exc: BaseException) -> str:
    return "\n".join([str(exc), repr(exc), repr(exc.args), "".join(traceback.format_exception(exc))])


# --- Model IDs (rule 5): only the cell's own in-region IDs -------------------------------------------------------

ARN = "arn:aws:bedrock:eu-west-2:123456789012:inference-profile/anthropic.claude-sonnet-5-test-v1:0"
BAD_IDS = {
    "global": "global.anthropic.claude-sonnet-5-test-v1:0",
    "eu": "eu.anthropic.claude-sonnet-5-test-v1:0",
    "us": "us.anthropic.claude-sonnet-5-test-v1:0",
    "apac": "apac.anthropic.claude-sonnet-5-test-v1:0",
    "us_gov": "us-gov.anthropic.claude-sonnet-5-test-v1:0",
    "arn_profile": ARN,
    "arn_foundation": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-5-test-v1:0",
    "other_vendor": "meta.llama3-test-v1:0",
    "uppercase": "ANTHROPIC.claude-sonnet-5-test-v1:0",
    "space_inside": "anthropic.claude sonnet-v1:0",
    "path_trick": "anthropic.claude/../global.x",
    "extra_version": "anthropic.claude-sonnet-5-test-v1:0:extra",
    "homoglyph": "anthropic.clаude-sonnet-5-test-v1:0",
    "leading_space": " anthropic.claude-sonnet-5-test-v1:0",
    "trailing_space": "anthropic.claude-sonnet-5-test-v1:0 ",
    "empty": "",
}


@pytest.mark.parametrize("model_id", sorted(BAD_IDS.values()))
def test_a_cross_region_profile_arn_or_odd_id_is_not_in_region(model_id: str) -> None:
    assert in_region_model_id(model_id) is False
    assert in_region_model_id(SONNET) is True  # control: the cell's own ID is accepted


def test_a_trailing_newline_is_not_part_of_an_in_region_id() -> None:
    assert in_region_model_id(SONNET + "\n") is False


@pytest.mark.parametrize("role_var", ["BEDROCK_SONNET_MODEL_ID", "BEDROCK_OPUS_MODEL_ID"])
@pytest.mark.parametrize("model_id", [v for k, v in sorted(BAD_IDS.items()) if v.strip() == v and v])
def test_config_refuses_a_bad_model_id_for_either_role_and_never_echoes_it(role_var: str, model_id: str) -> None:
    with pytest.raises(ConfigError) as caught:
        ai_settings(cell_env(**{role_var: model_id}))
    assert role_var in str(caught.value)
    assert model_id not in str(caught.value)
    assert ai_settings(cell_env()).models["sonnet"] == SONNET  # control: the cell's own config loads


@pytest.mark.parametrize("bad", [v for v in BAD_IDS.values() if v and v.strip() == v])
def test_config_refuses_a_bad_overflow_id_even_beside_a_good_one(bad: str) -> None:
    good = "anthropic.claude-overflow-test-v1:0"
    with pytest.raises(ConfigError, match="BEDROCK_OVERFLOW_MODEL_IDS"):
        ai_settings(cell_env(BEDROCK_OVERFLOW_MODEL_IDS=f"{good},{bad}"))
    settings = ai_settings(cell_env(BEDROCK_OVERFLOW_MODEL_IDS=good))  # control
    assert settings.overflow_models == (good,)
    assert check_model(settings, good) == good


@pytest.mark.parametrize("model_id", sorted(BAD_IDS.values()))
def test_check_model_refuses_every_bad_id_and_any_id_the_cell_did_not_configure(model_id: str) -> None:
    with pytest.raises(ModelNotAllowed):
        check_model(cell(), model_id)
    with pytest.raises(ModelNotAllowed):
        check_model(cell(), "anthropic.claude-not-configured-v1:0")  # the right shape is not enough
    assert check_model(cell(), SONNET) == SONNET and check_model(cell(), OPUS) == OPUS  # control


async def test_the_live_bedrock_client_refuses_a_bad_id_before_anything_is_sent() -> None:
    live = LiveBedrock(cell(), CREDS)
    sent: list[dict[str, Any]] = []
    live.client.converse = lambda **kw: sent.append(kw)
    for model_id in BAD_IDS.values():
        with pytest.raises(ModelNotAllowed):
            await live.invoke(request(model_id))
    assert sent == []
    with Stubber(live.client) as stub:  # control: the same call with the cell's ID goes out
        stub.add_response("converse", converse_response(VALID))
        live2 = LiveBedrock(cell(), CREDS)
        with Stubber(live2.client) as stub2:
            stub2.add_response("converse", converse_response(VALID))
            result = await live2.invoke(request(SONNET))
            stub2.assert_no_pending_responses()
    assert result.output == VALID


async def test_the_kit_refuses_a_task_with_a_model_role_the_cell_does_not_have() -> None:
    model = ScriptedBedrock([response(VALID)])
    evil = AiTask(task_id="T-test", prompt_version="v1", model="global.anthropic.evil", system="s", schema=SCHEMA)
    with pytest.raises(ModelNotAllowed):
        await AiKit(cell(), model).run(FakeConn(), evil, pack=PACK, text="x")
    assert model.requests == []
    await AiKit(cell(), model).run(FakeConn(), task(), pack=PACK, text="x")  # control
    assert [r.model_id for r in model.requests] == [SONNET]


def test_the_bedrock_endpoint_must_be_in_the_cells_own_region() -> None:
    other = "bedrock-runtime.us-east-1.amazonaws.com"
    env = cell_env(
        BEDROCK_ENDPOINT=f"https://{other}", AI_ENDPOINT_ALLOW_LIST=CELL_ENV["AI_ENDPOINT_ALLOW_LIST"] + f",{other}"
    )
    assert env["AWS_REGION"] == "eu-west-2"
    with pytest.raises(ConfigError):
        ai_settings(env)


# --- Endpoints: the allow-list is the cell's, matched on the real host -------------------------------------------

ALLOWED = [
    f"https://{BEDROCK_HOST}",
    f"https://{BEDROCK_HOST}/",
    f"https://{BEDROCK_HOST}/model/x/converse",
    f"HTTPS://{BEDROCK_HOST.upper()}",  # host names are not case sensitive
    f"https://{BEDROCK_HOST}:443",
]
REFUSED = {
    "http": f"http://{BEDROCK_HOST}",
    "ftp": f"ftp://{BEDROCK_HOST}",
    "file": f"file:///{BEDROCK_HOST}",
    "no_scheme": BEDROCK_HOST,
    "scheme_relative": f"//{BEDROCK_HOST}",
    "suffix_lookalike": f"https://{BEDROCK_HOST}.evil.example",
    "prefix_lookalike": f"https://evil-{BEDROCK_HOST}",
    "userinfo_trick": f"https://{BEDROCK_HOST}@evil.example",
    "userinfo_with_colon": f"https://{BEDROCK_HOST}:x@evil.example",
    "fragment_trick": f"https://evil.example#@{BEDROCK_HOST}",
    "query_trick": f"https://evil.example?@{BEDROCK_HOST}",
    "path_trick": f"https://evil.example/{BEDROCK_HOST}",
    "trailing_dot": f"https://{BEDROCK_HOST}.",
    "idn_homograph": "https://bеdrock-runtime.eu-west-2.amazonaws.com",
    "ideographic_stop": "https://bedrock-runtime。eu-west-2。amazonaws。com",
    "nul": f"https://{BEDROCK_HOST}%00",
    "empty_host": f"https:///{BEDROCK_HOST}",
    "other_host": "https://evil.example",
    "ipv6": "https://[::1]",
    "fullwidth_solidus": f"https://{BEDROCK_HOST}／x",
}


def refused(url: str) -> bool:
    try:
        return not endpoint_allowed(url, HOSTS)
    except ValueError:  # an unparsable address fails closed
        return True


@pytest.mark.parametrize("url", ALLOWED)
def test_the_exact_allowed_host_over_https_is_accepted(url: str) -> None:
    assert endpoint_allowed(url, HOSTS) is True


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_lookalike_or_insecure_endpoint_is_refused(name: str) -> None:
    assert refused(REFUSED[name]), name
    assert endpoint_allowed(f"https://{BEDROCK_HOST}", HOSTS)  # control: the real host is accepted


@pytest.mark.parametrize("url", [f"https://user@{BEDROCK_HOST}", f"https://user:pw@{BEDROCK_HOST}"])
def test_an_endpoint_with_userinfo_is_refused(url: str) -> None:
    assert refused(url)


@pytest.mark.parametrize("url", [f"https://{BEDROCK_HOST}:8443", f"https://{BEDROCK_HOST}:0"])
def test_a_non_default_port_is_refused(url: str) -> None:
    assert refused(url)


@pytest.mark.parametrize("url", [f" https://{BEDROCK_HOST}", f"https://{BEDROCK_HOST}\n", f"https://{BEDROCK_HOST}\t"])
def test_a_padded_endpoint_is_refused(url: str) -> None:
    assert refused(url)


def test_the_host_that_is_checked_is_the_host_that_the_http_layer_connects_to() -> None:
    url = f"https://evil.example\\@{BEDROCK_HOST}"
    assert parse_url(url).host == "evil.example"  # what the HTTP layer would connect to (proves the disagreement)
    assert refused(url)
    assert endpoint_allowed(f"https://{BEDROCK_HOST}", HOSTS)  # control


async def test_clients_and_kit_refuse_an_endpoint_off_the_allow_list_and_say_nothing_of_it() -> None:
    evil = f"https://evil.example/{CANARY}"
    with pytest.raises(EndpointNotAllowed) as caught:
        LiveBedrock(cell(), CREDS, endpoint=evil)
    assert CANARY not in str(caught.value)
    with pytest.raises(EndpointNotAllowed) as caught2:
        check_endpoint(cell(), evil)
    assert CANARY not in str(caught2.value) and "evil" not in str(caught2.value)
    assert LiveBedrock(cell(), CREDS).endpoint == CELL_ENV["BEDROCK_ENDPOINT"]  # control

    bad = ScriptedBedrock([response(VALID)], endpoint=evil)
    with pytest.raises(EndpointNotAllowed):
        await AiKit(cell(), bad).run(FakeConn(), task(), pack=PACK, text="x")
    assert bad.requests == []
    good = ScriptedBedrock([response(VALID)])
    assert await AiKit(cell(), good).run(FakeConn(), task(), pack=PACK, text="x") == VALID  # control

    direct_bad = ScriptedBedrock([response(VALID)], endpoint="https://api.anthropic.com.evil.example")
    with pytest.raises(EndpointNotAllowed):
        await AiKit(direct_settings(), good, direct=direct_bad).run(FakeConn(), task(), pack=PACK, text="x")
    assert direct_bad.requests == []


def test_the_direct_host_is_on_the_allow_list_only_while_the_direct_provider_is_on() -> None:
    assert "api.anthropic.com" not in cell().allowed_hosts
    with pytest.raises(EndpointNotAllowed):
        check_endpoint(cell(), "https://api.anthropic.com")
    with pytest.raises(EndpointNotAllowed):
        LiveAnthropic(cell(), KEY)  # no endpoint under the default provider: nothing to build
    on = direct_settings()
    assert check_endpoint(on, "https://api.anthropic.com") == "https://api.anthropic.com"  # control
    with pytest.raises(EndpointNotAllowed):
        check_endpoint(on, "https://api.anthropic.com.evil.example")
    with pytest.raises(EndpointNotAllowed):
        check_endpoint(on, "http://api.anthropic.com")


def test_config_errors_name_the_variable_and_never_echo_its_value() -> None:
    cases = {
        "BEDROCK_ENDPOINT": f"https://evil.example/{CANARY}",
        "AI_PRIMARY_PROVIDER": CANARY,
        "BEDROCK_SONNET_MODEL_ID": f"global.{CANARY}",
    }
    for name, value in cases.items():
        with pytest.raises(ConfigError) as caught:
            ai_settings(cell_env(**{name: value}, ANTHROPIC_API_KEY=KEY))
        assert name in str(caught.value) and CANARY not in exception_text(caught.value), name


def test_a_bad_number_in_config_does_not_chain_its_raw_value_into_the_traceback() -> None:
    with pytest.raises(ConfigError) as caught:
        ai_settings(cell_env(BEDROCK_RPM_LIMIT=CANARY))
    assert "BEDROCK_RPM_LIMIT" in str(caught.value)  # control: the name is there
    assert CANARY not in exception_text(caught.value)


# --- The temporary direct path (ADR 0036) ------------------------------------------------------------------------


def test_the_direct_provider_is_refused_in_a_production_cell() -> None:
    production = {"SENTRY_ENVIRONMENT": "production", "ENVIRONMENT": "production", "APP_ENV": "production"}
    with pytest.raises(ConfigError):
        ai_settings(cell_env(**ON, **production))


def test_the_client_factory_builds_no_direct_client_in_a_production_cell() -> None:
    production = {"SENTRY_ENVIRONMENT": "production", "ENVIRONMENT": "production", "APP_ENV": "production"}
    try:
        clients = live_or_recorded(cell_env(**ON, **AWS_KEYS, **production))
    except ConfigError:
        return
    assert clients.direct is None


async def test_the_direct_path_gets_only_stripped_text_its_own_model_id_and_never_an_opus_task() -> None:
    settings = direct_settings()
    direct = ScriptedBedrock([ServiceBusy()], endpoint="https://api.anthropic.com")
    fallback = ScriptedBedrock([response(VALID)])
    kit = AiKit(settings, fallback, direct=direct, sleep=no_sleep)
    text = f"Seen by {NAME} on the day. Labels follow."
    assert await kit.run(FakeConn(), task(), pack=PACK, text=text, names=(NAME,)) == VALID
    assert direct.requests, "control: the direct client was asked first"
    assert {r.model_id for r in direct.requests} == {DIRECT}
    assert [r.model_id for r in fallback.requests] == [SONNET]  # the fallback never sees the direct ID
    for sent in (*direct.requests, *fallback.requests):
        assert NAME not in sent.text and "Labels follow" in sent.text  # stripped, and not emptied

    opus = AiTask(task_id="T-test", prompt_version="v1", model="opus", system="s", schema=SCHEMA)
    direct2 = ScriptedBedrock([response(VALID)], endpoint="https://api.anthropic.com")
    fallback2 = ScriptedBedrock([response(VALID)])
    await AiKit(settings, fallback2, direct=direct2, sleep=no_sleep).run(FakeConn(), opus, pack=PACK, text="x")
    assert direct2.requests == [] and [r.model_id for r in fallback2.requests] == [OPUS]


async def test_a_call_pinned_to_bedrock_never_reaches_the_direct_client() -> None:
    direct = ScriptedBedrock([response(VALID)], endpoint="https://api.anthropic.com")
    fallback = ScriptedBedrock([response(VALID)])
    kit = AiKit(direct_settings(), fallback, direct=direct, sleep=no_sleep)
    await kit.run(FakeConn(), task(), pack=PACK, text="x", provider="bedrock")
    assert direct.requests == [] and len(fallback.requests) == 1
    await kit.run(FakeConn(), task(), pack=PACK, text="x")  # control: unpinned goes direct first
    assert len(direct.requests) == 1


def test_under_the_default_provider_there_is_no_direct_client_whatever_the_key_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    clients = live_or_recorded(cell_env(**AWS_KEYS, ANTHROPIC_API_KEY=KEY))
    assert clients.direct is None and clients.kit is not None and clients.kit.direct is None
    on = live_or_recorded(cell_env(**ON, **AWS_KEYS))
    assert isinstance(on.direct, LiveAnthropic)  # control: the switch is exactly AI_PRIMARY_PROVIDER


# --- Credentials never appear in a repr, a string, a log line or an exception ------------------------------------

SENTINELS = {
    "AWS_ACCESS_KEY_ID": "AKIAZXSENTINELID000",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-ZXSENTINEL",
    "AWS_SESSION_TOKEN": "aws-token-ZXSENTINEL",
    "AZURE_DI_KEY": "azure-key-ZXSENTINEL",
    "ANTHROPIC_API_KEY": "sk-ant-ZXSENTINEL-not-real",
}


def test_no_credential_is_in_the_repr_or_str_of_settings_clients_or_the_kit() -> None:
    clients = live_or_recorded(cell_env(AI_PRIMARY_PROVIDER="anthropic", **SENTINELS))
    assert clients.bedrock is not None and clients.direct is not None and clients.ocr is not None
    assert clients.kit is not None and clients.settings is not None
    objects: list[object] = [
        clients,
        clients.settings,
        clients.bedrock,
        clients.direct,
        clients.ocr,
        clients.kit,
        CREDS,
        vars(clients.kit),
        vars(clients.bedrock),
        vars(clients.direct),
        [clients.settings.models, clients.settings.limits, clients.settings.direct_models],
    ]
    blob = "\n".join(repr(o) + "\n" + str(o) for o in objects)
    for name, secret in SENTINELS.items():
        assert secret not in blob, name
    for secret in (CREDS.access_key_id, CREDS.secret_access_key, CREDS.session_token or ""):
        assert secret not in blob
    assert "LiveBedrock(endpoint=" in blob and "LiveAnthropic(endpoint=" in blob  # control: the reprs are real


def test_sentry_never_captures_local_variables_where_a_key_lives_as_a_parameter() -> None:
    options = sentry_options("https://public@o0.ingest.example/1", environment="uk")
    assert options["include_local_variables"] is False and options["send_default_pii"] is False


def test_the_cell_settings_hold_no_key_at_all() -> None:
    settings = ai_settings(cell_env(**ON, **{k: v for k, v in SENTINELS.items() if k != "ANTHROPIC_API_KEY"}))
    flat = repr(vars(settings)) + repr(settings)
    for secret in SENTINELS.values():
        assert secret not in flat


# --- Keys come only from the mapping given, never the SDK default chains -----------------------------------------


def test_the_factory_reads_only_the_mapping_it_is_given_never_the_process_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, secret in SENTINELS.items():
        monkeypatch.setenv(name, secret)
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_RECORDINGS_DIR", str(tmp_path))
    clients = live_or_recorded(cell_env())
    assert (clients.bedrock, clients.direct, clients.ocr, clients.kit) == (None, None, None, None)
    assert clients.settings is not None and clients.settings.primary_provider == "bedrock"
    with_keys = live_or_recorded(cell_env(**AWS_KEYS))  # control: the same call given keys builds a live client
    assert isinstance(with_keys.bedrock, LiveBedrock)


async def test_live_bedrock_signs_with_the_given_keys_and_goes_to_the_given_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = tmp_path / "credentials"
    profile.write_text("[default]\naws_access_key_id = FILEKEYZX\naws_secret_access_key = FILESECRETZX\n")
    for name, value in {
        "AWS_ACCESS_KEY_ID": "ENVKEYZX",
        "AWS_SECRET_ACCESS_KEY": "ENVSECRETZX",
        "AWS_SESSION_TOKEN": "ENVTOKENZX",
        "AWS_SHARED_CREDENTIALS_FILE": str(profile),
        "AWS_ENDPOINT_URL": "https://evil.example",
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "https://evil2.example",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    given = AwsCredentials(AWS_KEYS["AWS_ACCESS_KEY_ID"], AWS_KEYS["AWS_SECRET_ACCESS_KEY"])  # no session token
    live = LiveBedrock(cell(), given)
    signer = live.client._request_signer._credentials
    assert (signer.access_key, signer.secret_key) == (given.access_key_id, given.secret_access_key)
    assert signer.token is None  # the environment's token is not mixed in
    assert live.client.meta.endpoint_url == CELL_ENV["BEDROCK_ENDPOINT"]
    assert live.client.meta.region_name == CELL_ENV["AWS_REGION"]


def anthropic_client(
    handler: Callable[[httpx2.Request], httpx2.Response], *, key: str = KEY, settings: Any = None
) -> tuple[LiveAnthropic, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handle(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return handler(req)

    return LiveAnthropic(settings or direct_settings(), key, transport=httpx2.MockTransport(handle)), seen


def message(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": DIRECT,
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "record_output", "input": output}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


def answers(output: dict[str, Any] | None = None) -> Callable[[httpx2.Request], httpx2.Response]:
    body = sse_events(message(output or VALID))
    return lambda _req: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})


def sent_headers(req: httpx2.Request) -> str:
    return "\n".join(f"{k}: {v}" for k, v in req.headers.items())


@pytest.mark.parametrize("given", [KEY, ""])
async def test_live_anthropic_ignores_the_environments_key_token_and_base_url(
    monkeypatch: pytest.MonkeyPatch, given: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ENV-KEY-ZX")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ENV-TOKEN-ZX")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    client, seen = anthropic_client(answers(), key=given)
    result = await client.invoke(request(DIRECT))
    assert result.output == VALID and len(seen) == 1
    assert seen[0].url.host == "api.anthropic.com"
    headers = sent_headers(seen[0])
    assert "ENV-KEY-ZX" not in headers and "ENV-TOKEN-ZX" not in headers
    if given:
        assert given in headers  # control: the key it was given is the one sent


async def test_live_anthropic_sends_no_header_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Env-Injected: ZX-INJECTED")
    client, seen = anthropic_client(answers())
    await client.invoke(request(DIRECT))
    assert "ZX-INJECTED" not in sent_headers(seen[0])


# --- Redirects, retries and timeouts -----------------------------------------------------------------------------


async def test_live_anthropic_does_not_follow_a_redirect_to_another_host() -> None:
    live = LiveAnthropic(direct_settings(), KEY)  # the real wiring: no test transport, the SDK builds its client
    seen: list[httpx2.Request] = []

    def redirect(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        if req.url.host == "api.anthropic.com":
            return httpx2.Response(307, headers={"location": "https://evil.example/v1/messages"})
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}})

    live.client._client._transport = httpx2.MockTransport(redirect)
    with pytest.raises(ModelCallFailure):
        await live.invoke(request(DIRECT, text=f"stripped text {CANARY}"))
    assert seen[0].url.host == "api.anthropic.com" and CANARY.encode() in seen[0].content  # control: it was sent
    assert [r.url.host for r in seen if r.url.host != "api.anthropic.com"] == []


def timeouts(value: Any) -> tuple[float | None, float | None]:
    assert isinstance(value, httpx2.Timeout)
    return value.connect, value.read


def test_both_clients_set_connect_and_read_timeouts_and_turn_the_sdk_retries_off() -> None:
    bedrock = LiveBedrock(cell(), CREDS)
    config = bedrock.client.meta.config
    assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
    assert (config.connect_timeout, config.read_timeout) == (10.0, 300.0)

    live = LiveAnthropic(direct_settings(), KEY)
    assert live.client.max_retries == 0
    assert timeouts(live.client.timeout) == (10.0, 300.0)
    assert timeouts(live.client._client.timeout) == (10.0, 300.0)

    custom = LiveAnthropic(direct_settings(), KEY, connect_timeout=2.0, read_timeout=30.0)
    assert timeouts(custom.client.timeout) == (2.0, 30.0)  # control: they are passed on
    assert LiveBedrock(cell(), CREDS, connect_timeout=2.0, read_timeout=30.0).client.meta.config.read_timeout == 30.0


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 529])
async def test_a_failing_direct_call_is_one_http_request_the_sdk_does_not_retry(status: int) -> None:
    body = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    client, seen = anthropic_client(lambda _req: httpx2.Response(status, json=body))
    with pytest.raises(ModelCallFailure):
        await client.invoke(request(DIRECT))
    assert len(seen) == 1, f"status {status} was retried by the SDK"
    ok, seen_ok = anthropic_client(answers())  # control: a good answer is also one request
    await ok.invoke(request(DIRECT))
    assert len(seen_ok) == 1


class CountingModel:
    """A model that always fails the same way and counts every call, for worst-case arithmetic."""

    def __init__(self, failure: Exception | None, answer: dict[str, Any] | None = None, endpoint: str = "") -> None:
        self.failure, self.answer, self.calls = failure, answer, 0
        self.endpoint = endpoint or CELL_ENV["BEDROCK_ENDPOINT"]
        self.requests: list[ModelRequest] = []

    async def invoke(self, request: ModelRequest) -> Any:
        self.calls += 1
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return response(self.answer or VALID, input_tokens=500, output_tokens=50)


async def test_worst_case_provider_calls_for_one_run_when_the_model_is_always_busy() -> None:
    """Recorded: Bedrock only, default `retries=4`: 5 calls and 30 s of back-off, then `ProcessingPaused`. Each
    overflow model adds one call. With the direct provider first (`direct_retries=3`): 4 direct calls, then the
    same 5 on Bedrock, 9 in all."""
    sleeps = Sleeps()
    bedrock = CountingModel(ServiceBusy())
    kit = AiKit(cell(), bedrock, sleep=sleeps)
    with pytest.raises(ProcessingPaused) as caught:
        await kit.run(FakeConn(), task(), pack=PACK, text="x")
    assert caught.value.reason == "service_busy"
    assert bedrock.calls == kit.retries + 1 == 5
    assert sum(sleeps.seconds) == sum(backoff_delays(kit.retries, kit.base_delay)) <= 2 * 60 * 60

    overflow = ["anthropic.claude-overflow-a-v1:0", "anthropic.claude-overflow-b-v1:0"]
    settings = cell(BEDROCK_OVERFLOW_MODEL_IDS=",".join(overflow))
    wide = CountingModel(ServiceBusy())
    with pytest.raises(ProcessingPaused):
        await AiKit(settings, wide, sleep=no_sleep).run(FakeConn(), task(), pack=PACK, text="x")
    assert wide.calls == 5 + len(overflow)
    assert {r.model_id for r in wide.requests} == {SONNET, *overflow}

    direct = CountingModel(ServiceBusy(), endpoint="https://api.anthropic.com")
    both = CountingModel(ServiceBusy())
    dkit = AiKit(direct_settings(), both, direct=direct, sleep=no_sleep)
    with pytest.raises(ProcessingPaused):
        await dkit.run(FakeConn(), task(), pack=PACK, text="x")
    assert (direct.calls, both.calls) == (dkit.direct_retries + 1, dkit.retries + 1) == (4, 5)


async def test_worst_case_provider_calls_for_one_run_when_the_model_always_answers_badly() -> None:
    """Recorded: an answer that never matches the schema is asked for twice, then `InvalidOutput`. Both attempts are
    written to `ai_calls` with their tokens, so the cost guard sees what was spent."""
    model = CountingModel(None, answer=INVALID)
    conn = FakeConn()
    with pytest.raises(InvalidOutput):
        await AiKit(cell(), model, sleep=no_sleep).run(conn, task(), pack=PACK, text="x")
    assert model.calls == 2
    assert conn.outcomes == ["invalid_output", "invalid_output"]
    ok = CountingModel(None)  # control: a good answer is one call
    await AiKit(cell(), ok, sleep=no_sleep).run(FakeConn(), task(), pack=PACK, text="x")
    assert ok.calls == 1


async def test_worst_case_when_busy_then_bad_twice_stays_within_the_arithmetic() -> None:
    """Busy four times then a bad answer, twice over: the largest a single `run` can make with no overflow model is
    2 x (retries + 1) = 10 Bedrock calls; with the direct provider first it is at most 2 x (4 + 5) = 18."""
    kit_retries = 4
    script: list[Any] = [ServiceBusy()] * kit_retries + [response(INVALID)] + [ServiceBusy()] * kit_retries
    script += [response(INVALID)]
    model = ScriptedBedrock(script)
    with pytest.raises(InvalidOutput):
        await AiKit(cell(), model, sleep=no_sleep).run(FakeConn(), task(), pack=PACK, text="x")
    assert len(model.requests) == 2 * (kit_retries + 1)

    # Direct busy every time, Bedrock answers badly: the second try is pinned to the provider that answered.
    direct = ScriptedBedrock([ServiceBusy()], endpoint="https://api.anthropic.com")
    fallback = ScriptedBedrock([response(INVALID)])
    with pytest.raises(InvalidOutput):
        await AiKit(direct_settings(), fallback, direct=direct, sleep=no_sleep).run(
            FakeConn(), task(), pack=PACK, text="x"
        )
    assert len(direct.requests) + len(fallback.requests) <= 2 * (4 + 5)
    assert len(direct.requests) == 4 and len(fallback.requests) == 2


async def test_an_unavailable_or_refusing_model_is_asked_once() -> None:
    gone = CountingModel(ModelUnavailable("AccessDeniedException"))
    with pytest.raises(ProcessingPaused) as paused:
        await AiKit(cell(), gone, sleep=no_sleep).run(FakeConn(), task(), pack=PACK, text="x")
    assert paused.value.reason == "model_unavailable" and gone.calls == 1
    refusing = CountingModel(ModelRequestRefused("ValidationException"))
    with pytest.raises(ModelRequestRefused):
        await AiKit(cell(), refusing, sleep=no_sleep).run(FakeConn(), task(), pack=PACK, text="x")
    assert refusing.calls == 1


# --- The cost guard ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pages", [0, 50, 100, 4000])
async def test_a_tripped_cost_guard_stops_the_call_before_anything_is_sent(pages: int) -> None:
    settings = cell()
    allowance = settings.cost_guard.allowance(pages)
    case_id = __import__("uuid").uuid4()

    model = ScriptedBedrock([response(VALID)])
    tripped = FakeConn(budget=(allowance, pages))  # used == allowance trips it
    with pytest.raises(ProcessingPaused) as caught:
        await AiKit(settings, model).run(tripped, task(), pack=PACK, text="x", case_id=case_id)
    assert caught.value.reason == "cost_guard"
    assert model.requests == [] and tripped.logged == []  # nothing sent, no ai_calls row

    under = FakeConn(budget=(allowance - 1, pages))  # control: one token under goes through
    assert await AiKit(settings, model).run(under, task(), pack=PACK, text="x", case_id=case_id) == VALID
    assert len(model.requests) == 1


async def test_the_guard_applies_to_a_detached_worker_call_too() -> None:
    uuid = __import__("uuid")
    conn = FakeConn(budget=(10**12, 0))

    class Database:
        @contextlib.asynccontextmanager
        async def job(self, _case_id: Any) -> AsyncIterator[FakeConn]:
            yield conn

    model = ScriptedBedrock([response(VALID)])
    with pytest.raises(ProcessingPaused) as caught:
        await AiKit(cell(), model).run_detached(
            Database(),  # type: ignore[arg-type]
            uuid.uuid4(),
            task(),
            pack=PACK,
            text="x",
            job_id=uuid.uuid4(),
        )
    assert caught.value.reason == "cost_guard" and model.requests == []
    assert any("ai_budget" in sql for sql in conn.sql)  # control: the budget really was asked for


async def test_a_run_without_a_case_id_skips_the_guard_and_runs() -> None:
    """Recorded, by design: the guard is per case, so a call that belongs to no case has none."""
    model = ScriptedBedrock([response(VALID)])
    conn = FakeConn(budget=(10**12, 0))
    assert await AiKit(cell(), model).run(conn, task(), pack=PACK, text="x") == VALID
    assert not any("ai_budget" in sql for sql in conn.sql)
    tripped = FakeConn(budget=(10**12, 0))  # control: with a case id the same budget stops it
    with pytest.raises(ProcessingPaused):
        await AiKit(cell(), model).run(tripped, task(), pack=PACK, text="x", case_id=__import__("uuid").uuid4())


def kit_calls_without_a_case_id() -> list[str]:
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "kit.py" and path.parent.name == "ai":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"run", "run_image", "run_detached"}:
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            if "pack" not in keywords:  # not a kit call (asyncio.run, model sessions)
                continue
            value = keywords.get("case_id")
            if node.func.attr == "run_detached":
                continue  # case_id is a required positional argument
            if value is None or (isinstance(value, ast.Constant) and value.value is None):
                found.append(f"{path.relative_to(APP)}:{node.lineno}")
    return found


def test_the_only_kit_call_without_a_case_id_is_the_known_one() -> None:
    files = {entry.split(":")[0] for entry in kit_calls_without_a_case_id()}
    assert files == {"features/setup/routes_files.py"}, "a new unguarded caller (or the known one was fixed)"


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-14 M10: the T-02 route (suggest section mappings for a template) calls the kit with no case_id, so "
    "the per-case cost guard never applies. The route is user-reachable and bounded only by the global API rate "
    "limit and the kit's own call count: app/features/setup/routes_files.py:339",
)
def test_every_kit_call_carries_a_case_id_so_the_cost_guard_applies() -> None:
    assert kit_calls_without_a_case_id() == []


# --- Provider errors that echo the input never reach logs or raised text -----------------------------------------

ECHO = f"could not read: {CANARY} and the key {KEY}"


def echoing_body(kind: str = "invalid_request_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": ECHO, "param": CANARY}}


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 429, 500, 503, 529])
async def test_an_anthropic_error_that_echoes_the_input_is_neither_logged_nor_raised(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = anthropic_client(
        lambda _req: httpx2.Response(status, json=echoing_body(), headers={"request-id": "r1"})
    )
    with pytest.raises(ModelCallFailure) as caught:
        await client.invoke(request(DIRECT))
    text = log_text(caplog) + exception_text(caught.value)
    assert CANARY not in text and KEY not in text
    assert "outcome=" in log_text(caplog) and f"status={status}" in log_text(caplog)  # control: the failure was logged


@pytest.mark.parametrize("field", ["type", "code"])
async def test_an_echo_placed_in_the_error_type_or_code_is_dropped_by_the_allow_pattern(
    field: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body: dict[str, Any] = {"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
    body["error"][field] = f"{CANARY} echoed"
    client, _ = anthropic_client(lambda _req: httpx2.Response(400, json=body))
    with pytest.raises(ModelCallFailure) as caught:
        await client.invoke(request(DIRECT))
    assert CANARY not in log_text(caplog) + exception_text(caught.value)
    assert "status=400" in log_text(caplog)


@pytest.mark.parametrize("kind", ["overloaded_error", "invalid_request_error", "api_error"])
async def test_an_error_event_in_the_middle_of_a_stream_is_not_logged_or_raised_in_full(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    events = sse_events(message(VALID), fail_with=echoing_body(kind))
    client, _ = anthropic_client(
        lambda _req: httpx2.Response(200, content=events, headers={"content-type": "text/event-stream"})
    )
    with pytest.raises(ModelCallFailure) as caught:
        await client.invoke(request(DIRECT))
    assert CANARY not in log_text(caplog) + exception_text(caught.value)
    assert f"error_type={kind}" in log_text(caplog)


async def test_a_dropped_or_timed_out_direct_connection_is_logged_by_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    def boom(_req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"connection to https://x/{CANARY} failed")

    client, _ = anthropic_client(boom)
    with pytest.raises(ServiceBusy) as caught:
        await client.invoke(request(DIRECT))
    assert CANARY not in log_text(caplog) + exception_text(caught.value)
    assert "outcome=busy" in log_text(caplog) and "cause=APIConnectionError" in log_text(caplog)


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (f"event: message_start\ndata: {{not json {CANARY}\n\n".encode(), "text/event-stream"),
        (f"{CANARY} garbage".encode(), "application/json"),
    ],
)
async def test_a_malformed_direct_reply_becomes_a_typed_failure(body: bytes, content_type: str) -> None:
    client, _ = anthropic_client(
        lambda _req: httpx2.Response(200, content=body, headers={"content-type": content_type})
    )
    with pytest.raises(ModelCallFailure):
        await client.invoke(request(DIRECT))


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (f"event: message_start\ndata: {{not json {CANARY}\n\n".encode(), "text/event-stream"),
        (f"{CANARY} garbage".encode(), "application/json"),
    ],
)
async def test_a_malformed_direct_reply_leaks_no_text_into_logs_or_the_raised_message(
    body: bytes, content_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = anthropic_client(
        lambda _req: httpx2.Response(200, content=body, headers={"content-type": content_type})
    )
    with pytest.raises(Exception) as caught:  # noqa: PT011 - the type is the open question above
        await client.invoke(request(DIRECT))
    assert CANARY not in log_text(caplog) + exception_text(caught.value)
    assert isinstance(caught.value, Exception)  # control: something really was raised


BEDROCK_ERRORS = [
    ("ThrottlingException", 429),
    ("ValidationException", 400),
    ("AccessDeniedException", 403),
    ("ResourceNotFoundException", 404),
    ("InternalServerException", 500),
    ("ModelErrorException", 424),
    ("ServiceQuotaExceededException", 402),
]


@pytest.mark.parametrize(("code", "status"), BEDROCK_ERRORS)
async def test_a_bedrock_error_that_echoes_the_input_is_neither_logged_nor_raised(
    code: str, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code=code, service_message=ECHO, http_status_code=status)
        with pytest.raises(ModelCallFailure) as caught:
            await live.invoke(request(SONNET, text=f"text {CANARY}"))
    text = log_text(caplog) + exception_text(caught.value)
    assert CANARY not in text and KEY not in text
    assert f"status {status}" in log_text(caplog) or code == "ThrottlingException"  # control: it was logged


async def test_a_bedrock_timeout_is_logged_by_class_and_raised_as_busy(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    live = LiveBedrock(cell(), CREDS)

    def slow(**_kw: Any) -> Any:
        raise ReadTimeoutError(endpoint_url=f"https://x.example/{CANARY}")

    live.client.converse = slow
    with pytest.raises(ServiceBusy) as caught:
        await live.invoke(request(SONNET))
    assert CANARY not in log_text(caplog) + exception_text(caught.value)
    assert "ReadTimeoutError" in log_text(caplog)


async def test_an_unexpected_bedrock_error_code_is_not_trusted_into_logs_or_the_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code=f"Echo{CANARY.replace('-', '')}", http_status_code=400)
        with pytest.raises(ModelCallFailure) as caught:
            await live.invoke(request(SONNET))
    assert CANARY.replace("-", "") not in log_text(caplog) + exception_text(caught.value)


@pytest.mark.parametrize("provider", ["anthropic", "bedrock"])
async def test_the_provider_exception_is_not_kept_on_the_raised_failure(provider: str) -> None:
    if provider == "anthropic":
        client, _ = anthropic_client(lambda _req: httpx2.Response(500, json=echoing_body("api_error")))
        with pytest.raises(ModelCallFailure) as caught:
            await client.invoke(request(DIRECT))
    else:
        live = LiveBedrock(cell(), CREDS)
        with Stubber(live.client) as stub:
            stub.add_client_error("converse", "ValidationException", ECHO, 400)
            with pytest.raises(ModelCallFailure) as caught:
                await live.invoke(request(SONNET))
    context = caught.value.__context__
    assert context is None or CANARY not in str(context)
