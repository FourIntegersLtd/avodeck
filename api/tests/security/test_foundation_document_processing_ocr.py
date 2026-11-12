"""AUDIT-13 DP-01 (parser half), DP-08 and the redirect check: what comes back from the OCR service is validated,
capped and never followed off the cell's endpoint.

The service is a fake `httpx.MockTransport`; nothing reaches Azure. The canary stands for patient text: no error,
repr or log line may carry it (rule 6).

The rule for a malformed response (DP-01): any page or word that does not have the documented shape makes the WHOLE
response malformed (`OcrFailed("malformed_response")`), because a word with a short polygon or a confidence outside
0..1 cannot be stored (`app.ocr_words` checks both) and a partly read page is worse than a retry. An empty `pages`
list is fine and gives an `OcrResult` with no pages.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from app.core.ai.azure_ocr import AzureOcr
from app.core.ai.cell import EndpointNotAllowed
from app.core.ai.ocr import OcrFailed
from tests.support.ai import cell
from tests.support.live_ai import DI_KEY, AzureDi, Sleeps, di_page, di_result, succeeded

pytestmark = pytest.mark.anyio

CANARY = "CANARY-Jane-Doe-NHS-9434765919"
PAGE = b"%PDF-1.7 synthetic test page (no patient data)"


def ocr(di: AzureDi, **kwargs: Any) -> AzureOcr:
    return AzureOcr(cell(), DI_KEY, transport=di.transport(), sleep=Sleeps(), **kwargs)


def one_page() -> dict[str, Any]:
    return di_page(1, [CANARY, "today"])


def mutate(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    result = di_result([copy.deepcopy(one_page())])
    change(result["analyzeResult"])
    return result


def first_word(analysed: dict[str, Any]) -> dict[str, Any]:
    return analysed["pages"][0]["words"][0]  # type: ignore[no-any-return]


MALFORMED: dict[str, Callable[[dict[str, Any]], None]] = {
    "page number missing": lambda a: a["pages"][0].pop("pageNumber"),
    "page number not a number": lambda a: a["pages"][0].update(pageNumber="one"),
    "page number zero": lambda a: a["pages"][0].update(pageNumber=0),
    "page number a bool": lambda a: a["pages"][0].update(pageNumber=True),
    "page number repeated": lambda a: a["pages"].append(copy.deepcopy(a["pages"][0])),
    "page width not a number": lambda a: a["pages"][0].update(width="wide"),
    "page angle not finite": lambda a: a["pages"][0].update(angle=float("inf")),
    "pages not a list": lambda a: a.update(pages={"0": a["pages"][0]}),
    "pages hold a string": lambda a: a.update(pages=[CANARY]),
    "words not a list": lambda a: a["pages"][0].update(words=CANARY),
    "word not an object": lambda a: a["pages"][0]["words"].append(CANARY),
    "word content missing": lambda a: first_word(a).pop("content"),
    "word content not text": lambda a: first_word(a).update(content=7),
    "polygon missing": lambda a: first_word(a).pop("polygon"),
    "polygon too short": lambda a: first_word(a).update(polygon=[1.0, 1.0, 2.0, 1.0]),
    "polygon too long": lambda a: first_word(a).update(polygon=[1.0] * 10),
    "polygon not numbers": lambda a: first_word(a).update(polygon=[CANARY] * 8),
    "polygon not finite": lambda a: first_word(a).update(polygon=[float("nan")] * 8),
    "polygon not a list": lambda a: first_word(a).update(polygon=CANARY),
    "confidence missing": lambda a: first_word(a).pop("confidence"),
    "confidence above 1": lambda a: first_word(a).update(confidence=1.5),
    "confidence below 0": lambda a: first_word(a).update(confidence=-0.1),
    "confidence not a number": lambda a: first_word(a).update(confidence=CANARY),
    "span offset not a number": lambda a: first_word(a).update(span={"offset": CANARY, "length": 3}),
    "style span not a number": lambda a: a.update(styles=[{"isHandwritten": True, "spans": [{"offset": CANARY}]}]),
    "styles not a list": lambda a: a.update(styles=CANARY),
}


@pytest.mark.parametrize("name", list(MALFORMED))
async def test_a_malformed_response_is_ocr_failed_with_a_fixed_code_and_no_content(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    di = AzureDi(polls=[succeeded(mutate(MALFORMED[name]))])
    with pytest.raises(OcrFailed) as caught:
        await ocr(di).analyse(PAGE)
    error = caught.value
    assert str(error) == "malformed_response"
    assert CANARY not in str(error) + repr(error) + repr(error.args)
    assert error.__cause__ is None and error.__suppress_context__, "no chained KeyError or ValueError with content"
    assert CANARY not in caplog.text


@pytest.mark.parametrize(
    "body",
    [b"not json at all " + CANARY.encode(), b"[1, 2, 3]", b'"just a string"', b"null"],
)
async def test_a_body_that_is_not_a_json_object_is_malformed(body: bytes) -> None:
    di = AzureDi(polls=[httpx.Response(200, content=body)])
    with pytest.raises(OcrFailed) as caught:
        await ocr(di).analyse(PAGE)
    assert str(caught.value) == "malformed_response"
    assert CANARY not in repr(caught.value)


async def test_a_succeeded_answer_with_no_result_is_malformed() -> None:
    di = AzureDi(polls=[httpx.Response(200, json={"status": "succeeded"})])
    with pytest.raises(OcrFailed, match="malformed_response"):
        await ocr(di).analyse(PAGE)


async def test_an_empty_pages_list_is_a_result_with_no_pages() -> None:
    di = AzureDi(polls=[succeeded(di_result([]))])
    result = await ocr(di).analyse(PAGE)
    assert result.pages == ()


async def test_a_well_formed_response_still_parses_with_edge_values() -> None:
    page = di_page(2, ["a", "b"])
    page["words"][0]["confidence"] = 0
    page["words"][1]["confidence"] = 1
    page["words"][1]["polygon"] = [1, 1, 2, 1, 2, 2, 1, 2]  # whole numbers are numbers
    result = await ocr(AzureDi(polls=[succeeded(di_result([page]))])).analyse(PAGE)
    assert [w.confidence for w in result.pages[0].words] == [0.0, 1.0]


# --- DP-08: the answer has a size cap ------------------------------------------------------------------------------


async def test_an_oversized_answer_with_a_declared_length_is_refused_unread() -> None:
    big = httpx.Response(200, content=b"x" * 5000)
    with pytest.raises(OcrFailed) as caught:
        await ocr(AzureDi(polls=[big]), max_response_bytes=1000).analyse(PAGE)
    assert str(caught.value) == "response_too_large"


async def test_a_streamed_answer_with_no_length_is_cut_off_at_the_cap() -> None:
    sent = {"chunks": 0}

    async def body() -> AsyncIterator[bytes]:
        for _ in range(1000):
            sent["chunks"] += 1
            yield b"x" * 1000

    di = AzureDi(polls=[httpx.Response(200, content=body())])
    with pytest.raises(OcrFailed) as caught:
        await ocr(di, max_response_bytes=5000).analyse(PAGE)
    assert str(caught.value) == "response_too_large"
    assert sent["chunks"] < 20, "the rest of the body was never read"


async def test_a_compressed_answer_is_capped_after_it_is_unpacked() -> None:
    import gzip

    squeezed = gzip.compress(b"x" * 200_000)
    assert len(squeezed) < 2000
    response = httpx.Response(200, content=squeezed, headers={"Content-Encoding": "gzip"})
    with pytest.raises(OcrFailed, match="response_too_large"):
        await ocr(AzureDi(polls=[response]), max_response_bytes=10_000).analyse(PAGE)


async def test_an_answer_within_the_cap_is_read_whole() -> None:
    di = AzureDi(polls=[succeeded(di_result([di_page(1, ["ok"])]))])
    result = await ocr(di, max_response_bytes=100_000).analyse(PAGE)
    assert result.pages[0].words[0].text == "ok"


async def test_the_default_cap_comes_from_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_OCR_RESPONSE_BYTES", "1000")
    with pytest.raises(OcrFailed, match="response_too_large"):
        await ocr(AzureDi(polls=[httpx.Response(200, content=b"x" * 5000)])).analyse(PAGE)


# --- redirects are not followed ------------------------------------------------------------------------------------


async def test_a_redirect_from_the_service_is_not_followed_and_the_key_goes_nowhere_else() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "evil.example":
            return httpx.Response(202, headers={"Operation-Location": "https://evil.example/x"})
        return httpx.Response(307, headers={"Location": "https://evil.example/steal"})

    client = AzureOcr(cell(), DI_KEY, transport=httpx.MockTransport(handler), sleep=Sleeps())
    with pytest.raises(OcrFailed):
        await client.analyse(PAGE)
    assert [r.url.host for r in seen] == [r.url.host for r in seen[:1]], "only the cell's own host was called"
    assert all(r.url.host != "evil.example" for r in seen)


async def test_an_operation_location_off_the_cell_is_refused_before_any_poll() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, headers={"Operation-Location": "https://evil.example/poll"})

    client = AzureOcr(cell(), DI_KEY, transport=httpx.MockTransport(handler), sleep=Sleeps())
    with pytest.raises(EndpointNotAllowed):
        await client.analyse(PAGE)
    assert len(seen) == 1
