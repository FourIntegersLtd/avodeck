"""The live OCR client (Azure AI Document Intelligence, the cell's region) behind `OcrClient`.

Every test answers with recorded HTTP (`httpx.MockTransport`) shaped like the real v4 API: submit
returns 202 with `Operation-Location`, polls return `running` then `succeeded`. Nothing reaches Azure.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.ai.azure_ocr import AzureOcr

from app.core.ai.bedrock import ServiceBusy
from app.core.ai.cell import EndpointNotAllowed
from app.core.ai.ocr import OcrClient, OcrFailed, OcrResult
from tests.support.ai import cell
from tests.support.live_ai import (
    API_VERSION,
    DI_ENDPOINT,
    DI_KEY,
    AzureDi,
    Sleeps,
    di_page,
    di_result,
    operation_url,
    running,
    succeeded,
)

pytestmark = pytest.mark.anyio

PAGE = b"%PDF-1.7 synthetic test page (no patient data)"


def client(di: AzureDi, sleeps: Sleeps | None = None, **kwargs: object) -> AzureOcr:
    return AzureOcr(cell(), DI_KEY, transport=di.transport(), sleep=sleeps or Sleeps(), **kwargs)  # type: ignore[arg-type]


def test_it_is_an_ocr_client_on_the_cell_endpoint() -> None:
    ocr = AzureOcr(cell(), DI_KEY)
    assert isinstance(ocr, OcrClient)
    assert ocr.endpoint == DI_ENDPOINT
    assert DI_KEY not in repr(ocr), "the key never appears in a repr"


async def test_layout_submits_the_page_polls_and_maps_words_in_reading_order() -> None:
    page = di_page(1, ["Seen", "today", "Amoxicillin", "500mg"])
    di = AzureDi(polls=[running(), succeeded(di_result([page], handwritten=[{"offset": 11, "length": 11}]))])
    sleeps = Sleeps()
    result = await client(di, sleeps).analyse(PAGE, model="layout")

    submit, *polls = di.requests
    assert submit.method == "POST"
    assert str(submit.url) == (
        f"{DI_ENDPOINT}/documentintelligence/documentModels/prebuilt-layout:analyze?api-version={API_VERSION}"
    )
    assert submit.headers["Ocp-Apim-Subscription-Key"] == DI_KEY
    assert submit.headers["Content-Type"] == "application/octet-stream"
    assert submit.content == PAGE
    assert [p.method for p in polls] == ["GET", "GET"]
    assert str(polls[0].url) == operation_url()
    assert all(p.headers["Ocp-Apim-Subscription-Key"] == DI_KEY for p in polls)
    assert sleeps.waits == [1.0, 1.0], "polls wait as long as Retry-After says"

    assert isinstance(result, OcrResult)
    assert result.model == "layout"
    [found] = result.pages
    assert (found.number, found.width, found.height) == (1, 8.5, 11.0)
    assert [w.id for w in found.words] == ["p1w1", "p1w2", "p1w3", "p1w4"]
    assert [w.text for w in found.words] == ["Seen", "today", "Amoxicillin", "500mg"]
    assert found.words[0].polygon == (1.0, 1.0, 1.5, 1.0, 1.5, 1.2, 1.0, 1.2)
    assert found.words[0].confidence == pytest.approx(0.9)
    assert [w.handwritten for w in found.words] == [False, False, True, False], "handwriting comes from styles"
    assert not any(w.struck for w in found.words)
    assert "Amoxicillin" not in repr(result)


async def test_read_uses_the_read_model_and_numbers_each_page() -> None:
    pages = [di_page(1, ["one", "two"]), di_page(2, ["three"], offset=8)]
    di = AzureDi(polls=[succeeded(di_result(pages, model_id="prebuilt-read"))])
    result = await client(di).analyse(PAGE, model="read")
    assert "/documentModels/prebuilt-read:analyze" in str(di.requests[0].url)
    assert result.model == "read"
    assert [[w.id for w in p.words] for p in result.pages] == [["p1w1", "p1w2"], ["p2w1"]]


async def test_a_busy_service_is_retried_with_backoff_honouring_retry_after() -> None:
    di = AzureDi(
        submits=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(202, headers={"Operation-Location": operation_url()}),
        ],
        polls=[httpx.Response(503), succeeded(di_result([di_page(1, ["ok"])]))],
    )
    sleeps = Sleeps()
    result = await client(di, sleeps, base_delay=2.0).analyse(PAGE)
    assert [w.id for w in result.pages[0].words] == ["p1w1"]
    assert [r.method for r in di.requests] == ["POST", "POST", "GET", "GET"]
    assert sleeps.waits[0] == 7.0, "Retry-After wins over a shorter back-off"
    assert len(sleeps.waits) == 3


async def test_a_service_that_stays_busy_raises_service_busy() -> None:
    di = AzureDi(submits=[httpx.Response(503)], polls=[])
    sleeps = Sleeps()
    with pytest.raises(ServiceBusy):
        await client(di, sleeps, retries=3, base_delay=1.0).analyse(PAGE)
    assert len(di.requests) == 4
    assert sleeps.waits == [1.0, 2.0, 4.0]


async def test_timeouts_and_dropped_connections_are_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        if calls["n"] == 2:
            raise httpx.ConnectError("refused", request=request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": operation_url()})
        return succeeded(di_result([di_page(1, ["ok"])]))

    ocr = AzureOcr(cell(), DI_KEY, transport=httpx.MockTransport(handler), sleep=Sleeps())
    result = await ocr.analyse(PAGE)
    assert calls["n"] == 4
    assert len(result.pages) == 1


async def test_a_job_that_never_finishes_gives_up_as_busy() -> None:
    di = AzureDi(polls=[httpx.Response(200, json={"status": "running"})])
    sleeps = Sleeps()
    with pytest.raises(ServiceBusy):
        await client(di, sleeps, poll_interval=10.0, max_wait=60.0).analyse(PAGE)
    assert sum(sleeps.waits) <= 60.0


async def test_a_failed_analysis_raises_ocr_failed_without_the_service_message() -> None:
    body = {"status": "failed", "error": {"code": "InvalidContent", "message": "Ada Pemberton-Vale page is corrupt"}}
    di = AzureDi(polls=[httpx.Response(200, json=body)])
    with pytest.raises(OcrFailed) as caught:
        await client(di).analyse(PAGE)
    assert "Pemberton" not in str(caught.value)
    assert "InvalidContent" in str(caught.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
async def test_a_refused_request_is_not_retried(status: int) -> None:
    di = AzureDi(submits=[httpx.Response(status, json={"error": {"code": "Refused", "message": "no"}})], polls=[])
    sleeps = Sleeps()
    with pytest.raises(OcrFailed):
        await client(di, sleeps).analyse(PAGE)
    assert len(di.requests) == 1 and sleeps.waits == []


@pytest.mark.parametrize(
    "location",
    [
        operation_url(base="https://elsewhere.cognitiveservices.azure.com"),
        operation_url(base="http://avodeck-test-uks.cognitiveservices.azure.com"),
    ],
)
async def test_an_operation_location_off_the_allow_list_is_never_followed(location: str) -> None:
    di = AzureDi(polls=[succeeded(di_result([di_page(1, ["ok"])]))], operation=location)
    with pytest.raises(EndpointNotAllowed):
        await client(di).analyse(PAGE)
    assert [r.method for r in di.requests] == ["POST"]


def test_an_endpoint_off_the_allow_list_is_refused_when_made() -> None:
    with pytest.raises(EndpointNotAllowed):
        AzureOcr(cell(), DI_KEY, endpoint="https://elsewhere.cognitiveservices.azure.com")


def test_an_empty_key_is_refused() -> None:
    with pytest.raises(ValueError):
        AzureOcr(cell(), "")


async def test_the_request_body_is_the_document_and_nothing_else() -> None:
    di = AzureDi(polls=[succeeded(di_result([di_page(1, ["ok"])]))])
    await client(di).analyse(PAGE)
    assert di.requests[0].content == PAGE
    with pytest.raises(json.JSONDecodeError):
        json.loads(di.requests[0].content)
