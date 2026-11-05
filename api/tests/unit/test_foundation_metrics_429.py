"""A provider's 429 is counted where the live clients map it to `ServiceBusy`."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from botocore.stub import Stubber

from app.core import metrics
from app.core.ai.azure_ocr import AzureOcr
from app.core.ai.bedrock import ServiceBusy
from app.core.ai.bedrock_live import AwsCredentials, LiveBedrock
from tests.support.ai import cell
from tests.support.live_ai import AWS_KEYS, DI_KEY, AzureDi, Sleeps
from tests.unit.test_foundation_live_bedrock import request

pytestmark = pytest.mark.anyio

CREDS = AwsCredentials(AWS_KEYS["AWS_ACCESS_KEY_ID"], AWS_KEYS["AWS_SECRET_ACCESS_KEY"])
PAGE = b"%PDF-1.7 synthetic test page (no patient data)"


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    metrics.reset()
    yield
    metrics.reset()


async def test_a_bedrock_throttle_is_counted() -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)
        with pytest.raises(ServiceBusy):
            await live.invoke(request())
    assert metrics.snapshot()["provider_429_total"]["bedrock"] == 1


async def test_a_bedrock_outage_is_not_counted_as_a_429() -> None:
    live = LiveBedrock(cell(), CREDS)
    with Stubber(live.client) as stub:
        stub.add_client_error("converse", service_error_code="ServiceUnavailableException", http_status_code=503)
        with pytest.raises(ServiceBusy):
            await live.invoke(request())
    assert metrics.snapshot()["provider_429_total"]["bedrock"] == 0


async def test_an_azure_429_is_counted_each_time_but_a_503_is_not() -> None:
    di = AzureDi(submits=[httpx.Response(429), httpx.Response(429), httpx.Response(503)], polls=[])
    ocr = AzureOcr(cell(), DI_KEY, transport=di.transport(), sleep=Sleeps(), retries=2)
    with pytest.raises(ServiceBusy):
        await ocr.analyse(PAGE)
    assert metrics.snapshot()["provider_429_total"]["azure_ocr"] == 2
