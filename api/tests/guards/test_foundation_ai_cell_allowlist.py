"""Rule 5: processing stays in the home region (architecture 2.3, 6.1; failure mode F24).

The cell configuration names its in-region model IDs and its AI endpoints. Settings refuse a
cross-region inference profile (`global.*`, `eu.*`, ...) or an ARN; every client, the stand-ins
included, refuses an endpoint outside the cell's allow-list; Bedrock refuses any model ID outside
it; and nothing region-specific is written in the AI kit's code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.ai.bedrock import ModelRequest, RecordedBedrock
from app.core.ai.cell import EndpointNotAllowed, ModelNotAllowed, check_endpoint, check_model
from app.core.ai.ocr import RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.config import ConfigError, ai_settings
from app.core.speech import RecordedSpeech
from tests.support.ai import CELL_ENV, cell, cell_env

pytestmark = pytest.mark.anyio

API = Path(__file__).resolve().parents[2]
SONNET = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]


def test_the_cell_names_its_models_region_and_endpoints() -> None:
    settings = cell()
    assert settings.models == {"sonnet": SONNET, "opus": CELL_ENV["BEDROCK_OPUS_MODEL_ID"]}
    assert settings.bedrock_region == "eu-west-2"
    assert settings.allowed_hosts == frozenset(CELL_ENV["AI_ENDPOINT_ALLOW_LIST"].split(","))


@pytest.mark.parametrize(
    "model_id",
    [
        "global.anthropic.claude-sonnet-5-test-v1:0",
        "eu.anthropic.claude-sonnet-5-test-v1:0",
        "us.anthropic.claude-sonnet-5-test-v1:0",
        "apac.anthropic.claude-sonnet-5-test-v1:0",
        "arn:aws:bedrock:eu-west-2:000000000000:inference-profile/eu.anthropic.claude-sonnet-5-test-v1:0",
        "arn:aws:bedrock:eu-central-1::foundation-model/anthropic.claude-sonnet-5-test-v1:0",
        "",
    ],
)
def test_settings_refuse_a_model_id_that_is_not_in_region(model_id: str) -> None:
    with pytest.raises(ConfigError):
        ai_settings(cell_env(BEDROCK_SONNET_MODEL_ID=model_id))


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("BEDROCK_ENDPOINT", "https://bedrock-runtime.eu-central-1.amazonaws.com"),
        ("AZURE_DI_ENDPOINT", "https://somewhere-else.cognitiveservices.azure.com"),
        ("AZURE_SPEECH_ENDPOINT", "wss://westeurope.stt.speech.microsoft.com"),
        ("BEDROCK_ENDPOINT", "http://bedrock-runtime.eu-west-2.amazonaws.com"),
    ],
)
def test_settings_refuse_an_endpoint_outside_the_allow_list(name: str, url: str) -> None:
    with pytest.raises(ConfigError):
        ai_settings(cell_env(**{name: url}))


def test_settings_need_every_ai_value() -> None:
    env = cell_env()
    del env["AI_ENDPOINT_ALLOW_LIST"]
    with pytest.raises(ConfigError):
        ai_settings(env)


def test_model_and_endpoint_checks() -> None:
    settings = cell()
    assert check_model(settings, SONNET) == SONNET
    for bad in ("anthropic.some-other-model-v1:0", "eu." + SONNET, "global." + SONNET):
        with pytest.raises(ModelNotAllowed):
            check_model(settings, bad)
    assert check_endpoint(settings, CELL_ENV["BEDROCK_ENDPOINT"]) == CELL_ENV["BEDROCK_ENDPOINT"]
    with pytest.raises(EndpointNotAllowed):
        check_endpoint(settings, "https://bedrock-runtime.us-east-1.amazonaws.com")


async def test_bedrock_refuses_a_model_outside_the_allow_list_before_sending(tmp_path: Path) -> None:
    settings = cell()
    client = RecordedBedrock(settings, FixtureStore(tmp_path))
    request = ModelRequest(model_id="eu." + SONNET, system="Label.", context={}, text="text", schema={})
    with pytest.raises(ModelNotAllowed):
        await client.invoke(request)
    assert client.requests == []


def test_every_client_refuses_an_endpoint_outside_the_allow_list(tmp_path: Path) -> None:
    settings = cell()
    store = FixtureStore(tmp_path)
    with pytest.raises(EndpointNotAllowed):
        RecordedBedrock(settings, store, endpoint="https://bedrock-runtime.eu-central-1.amazonaws.com")
    with pytest.raises(EndpointNotAllowed):
        RecordedOcr(settings, store, endpoint="https://elsewhere.cognitiveservices.azure.com")
    with pytest.raises(EndpointNotAllowed):
        RecordedSpeech(settings, store, endpoint="wss://westeurope.stt.speech.microsoft.com")


def test_no_region_or_vendor_host_is_written_in_the_ai_kit() -> None:
    pattern = re.compile(r"eu-west-2|uksouth|uk-south|amazonaws\.com|microsoft\.com|azure\.com|\bglobal\.anthropic")
    files = [*sorted((API / "app" / "core" / "ai").rglob("*.py")), API / "app" / "core" / "speech.py"]
    assert len(files) >= 6
    found = [str(f.relative_to(API)) for f in files if pattern.search(f.read_text())]
    assert found == []
