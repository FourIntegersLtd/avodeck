"""Rule 6 for the live AI clients: page text, OCR output, prompts, answers and identifiers never reach
a log line or an error message, on success or failure. The clients log only counts, durations,
status codes and request IDs. The AWS SDK's own debug logging (which prints request bodies) is
held at WARNING so it can never carry a prompt.

Everything answers with recorded HTTP; nothing reaches Azure or AWS.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from app.core.ai.azure_ocr import AzureOcr
from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock
from botocore.stub import Stubber

from app.core.ai.bedrock import ModelRequest
from tests.support.ai import CELL_ENV, NAME, cell, mod11_number, spaced
from tests.support.live_ai import (
    AWS_KEYS,
    DI_KEY,
    AzureDi,
    Sleeps,
    converse_response,
    di_page,
    di_result,
    operation_url,
    succeeded,
)

pytestmark = pytest.mark.anyio

NUMBER = mod11_number("943476591")
SECRETS = (NUMBER, spaced(NUMBER), "Pemberton", NAME.split()[0] + " ", DI_KEY, AWS_KEYS["AWS_SECRET_ACCESS_KEY"])
PAGE = f"%PDF-1.7 {NAME} {NUMBER}".encode()


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, "patient text or a key reached a log line"


async def test_ocr_logs_counts_and_status_never_words(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    words = [*NAME.split(), *spaced(NUMBER).split(), NUMBER]
    di = AzureDi(
        submits=[httpx.Response(429), httpx.Response(202, headers={"Operation-Location": operation_url()})],
        polls=[httpx.Response(503), succeeded(di_result([di_page(1, words)]))],
    )
    result = await AzureOcr(cell(), DI_KEY, transport=di.transport(), sleep=Sleeps()).analyse(PAGE)
    assert len(result.pages[0].words) == len(words)
    assert any(r.name.startswith("app.ai") for r in caplog.records), "the client logs what it did"
    assert_clean(caplog.text)
    assert_clean(repr(result))


async def test_a_failed_ocr_keeps_the_service_message_out(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    body = {"status": "failed", "error": {"code": "InvalidContent", "message": f"{NAME} {NUMBER}"}}
    di = AzureDi(polls=[httpx.Response(200, json=body)])
    with pytest.raises(Exception) as caught:
        await AzureOcr(cell(), DI_KEY, transport=di.transport(), sleep=Sleeps()).analyse(PAGE)
    assert_clean(str(caught.value))
    assert_clean(caplog.text)


def _request() -> ModelRequest:
    return ModelRequest(
        model_id=CELL_ENV["BEDROCK_SONNET_MODEL_ID"],
        system=f"Label the page for {NAME}.",
        context={"subject": NAME},
        text=f"{NAME} {spaced(NUMBER)}",
        schema={"type": "object"},
    )


async def test_the_model_client_logs_tokens_and_status_never_prompts_or_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    live = LiveBedrock(cell(), AwsCredentials(AWS_KEYS["AWS_ACCESS_KEY_ID"], AWS_KEYS["AWS_SECRET_ACCESS_KEY"]))
    with Stubber(live.client) as stub:
        stub.add_response("converse", converse_response({"who": NAME, "number": NUMBER}))
        stub.add_client_error("converse", service_error_code="ValidationException", service_message=f"{NAME} {NUMBER}")
        response = await live.invoke(_request())
        with pytest.raises(Exception) as caught:
            await live.invoke(_request())
    assert response.output["who"] == NAME
    assert any(r.name.startswith("app.ai") for r in caplog.records), "the client logs what it did"
    assert_clean(caplog.text)
    assert_clean(str(caught.value))
    assert_clean(repr(response) + repr(_request()))


def test_the_aws_sdk_never_logs_request_bodies() -> None:
    import app.core.ai.bedrock_live  # noqa: F401  (pins the SDK loggers on import)

    for name in ("botocore", "boto3", "urllib3"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
