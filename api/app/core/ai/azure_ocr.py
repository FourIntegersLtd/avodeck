"""The live OCR client: Azure AI Document Intelligence v4 (REST `2024-11-30`) in the cell's region.

`analyse` sends the document to the prebuilt Layout or Read model, polls the `Operation-Location`
the service returns (only if it is on the cell's allow-list and the same host), and maps the result
to `OcrResult`: word IDs `p<page>w<n>` in the service's reading order, 8-number polygons in the
page's unit (inches for PDF), confidence, and handwriting from the result's `styles` spans. The
service does not report crossed-out text, so `struck` stays False.

Busy (429, 5xx) or unreachable: growing waits honouring `Retry-After`, then `ServiceBusy`. Refused
(other 4xx) or a failed analysis: `OcrFailed` with a status or error code only. A body that is not the documented
shape is `OcrFailed("malformed_response")`; one over `MAX_OCR_RESPONSE_BYTES` (read as a stream, after unpacking)
is `OcrFailed("response_too_large")`. Redirects are never followed. Logs carry counts, durations, status codes and
request IDs, never words (rule 6).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core import metrics
from app.core.ai.bedrock import ServiceBusy
from app.core.ai.cell import EndpointNotAllowed, check_endpoint
from app.core.ai.ocr import OcrFailed, OcrModel, OcrPage, OcrResult, OcrWord, word_id
from app.core.config import AiSettings, document_limits
from app.core.http_retry import MAX_RETRY_AFTER, backoff, parse_retry_after

logger = logging.getLogger("app.ai.ocr")

API_VERSION = "2024-11-30"
MODEL_IDS: dict[str, str] = {"layout": "prebuilt-layout", "read": "prebuilt-read"}
_BUSY = frozenset({408, 429, 500, 502, 503, 504})
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _retry_after(response: httpx.Response) -> float | None:
    return parse_retry_after(response.headers.get("Retry-After"))


class AzureOcr:
    kind = "ocr"

    def __init__(
        self,
        settings: AiSettings,
        key: str,
        *,
        endpoint: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retries: int = 4,
        base_delay: float = 2.0,
        poll_interval: float = 1.0,
        max_wait: float = 300.0,
        timeout: httpx.Timeout | None = None,
        max_response_bytes: int | None = None,
    ) -> None:
        if not key:
            raise ValueError("an OCR key is needed")
        self.settings = settings
        self.endpoint = check_endpoint(settings, endpoint or settings.ocr_endpoint)
        self._key = key
        self._transport = transport
        self._sleep = sleep
        self._retries = retries
        self._base_delay = base_delay
        self._poll_interval = poll_interval
        self._max_wait = max_wait
        self._timeout = timeout or httpx.Timeout(60.0, connect=10.0)
        self._max_bytes = max_response_bytes or document_limits().max_ocr_response_bytes

    def __repr__(self) -> str:
        return f"AzureOcr(endpoint={self.endpoint!r})"

    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        started = time.monotonic()
        model_id = MODEL_IDS[model]
        url = f"{self.endpoint.rstrip('/')}/documentintelligence/documentModels/{model_id}:analyze"
        params = {"api-version": API_VERSION}
        if locale:
            params["locale"] = locale
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            submitted = await self._send(
                http,
                "POST",
                url,
                params=params,
                content=document,
                headers={"Content-Type": "application/octet-stream"},
            )
            location = submitted.headers.get("Operation-Location")
            if submitted.status_code != 202 or not location:
                raise OcrFailed(f"http_{submitted.status_code}")
            self._check_location(location)
            data, polls = await self._poll(http, location, _retry_after(submitted))
        result = _result(model, data.get("analyzeResult"))
        logger.info(
            "ocr %s done: %d pages, %d words in %d ms after %d polls (request %s)",
            model,
            len(result.pages),
            sum(len(p.words) for p in result.pages),
            (time.monotonic() - started) * 1000,
            polls,
            submitted.headers.get("apim-request-id", "-"),
        )
        return result

    def _check_location(self, location: str) -> None:
        check_endpoint(self.settings, location)
        if urlsplit(location).hostname != urlsplit(self.endpoint).hostname:
            raise EndpointNotAllowed("operation location is not on this cell's OCR endpoint")

    async def _poll(self, http: httpx.AsyncClient, location: str, first_wait: float | None) -> tuple[Any, int]:
        waited, polls, wait = 0.0, 0, first_wait
        while True:
            pause = wait if wait is not None else self._poll_interval
            if waited + pause > self._max_wait:
                logger.error("ocr still running after %.0f s; giving up for now", waited)
                raise ServiceBusy()
            await self._sleep(pause)
            waited += pause
            response = await self._send(http, "GET", location)
            polls += 1
            if response.status_code != 200:
                raise OcrFailed(f"http_{response.status_code}")
            data = _json_object(response)
            status = data.get("status")
            if status == "succeeded":
                return data, polls
            if status in ("failed", "canceled"):
                code = str((data.get("error") or {}).get("code") or status)
                logger.error("ocr analysis %s (code %s)", status, code if _CODE.match(code) else "-")
                raise OcrFailed(code if _CODE.match(code) else status)
            wait = _retry_after(response)

    async def _request(self, http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One request whose answer is read as a stream and stopped at the cap (after unpacking, so a compressed
        bomb counts at its real size). Redirects are not followed (the client's default), so the key goes only to the
        cell's endpoint. The answer comes back whole, as an ordinary `httpx.Response`."""
        request = http.build_request(method, url, **kwargs)
        response = await http.send(request, stream=True)
        try:
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > self._max_bytes:
                raise OcrFailed("response_too_large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise OcrFailed("response_too_large")
        finally:
            await response.aclose()
        kept = [
            (k, v)
            for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        ]
        return httpx.Response(response.status_code, headers=kept, content=bytes(body), request=request)

    async def _send(self, http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Ocp-Apim-Subscription-Key": self._key, **kwargs.pop("headers", {})}
        for attempt in range(self._retries + 1):
            retry_after: float | None = None
            try:
                response = await self._request(http, method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                status: str = type(exc).__name__
            else:
                if response.status_code not in _BUSY:
                    if response.status_code >= 400:
                        logger.warning("ocr %s refused with status %d", method, response.status_code)
                        raise OcrFailed(f"http_{response.status_code}")
                    return response
                status, retry_after = str(response.status_code), _retry_after(response)
                if response.status_code == 429:
                    metrics.count_429("azure_ocr")
            if attempt == self._retries:
                logger.error("ocr service still busy (%s) after %d attempts", status, attempt + 1)
                raise ServiceBusy(retry_after)
            pause = max(backoff(attempt, self._base_delay, MAX_RETRY_AFTER, jitter=0.0), retry_after or 0.0)
            logger.warning("ocr service busy (%s); waiting %.0f s (attempt %d)", status, pause, attempt + 1)
            await self._sleep(pause)
        raise AssertionError("unreachable")  # pragma: no cover


_MALFORMED = "malformed_response"


class _Malformed(Exception):
    """Internal: the answer is not the documented shape. Never leaves this module; carries nothing."""


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise OcrFailed(_MALFORMED) from None
    if not isinstance(data, dict):
        raise OcrFailed(_MALFORMED)
    return data


def _number(value: Any, *, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Malformed
    if (low is not None and value < low) or (high is not None and value > high):
        raise _Malformed
    return float(value)


def _whole(value: Any, *, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise _Malformed
    return value


def _objects(value: Any) -> list[dict[str, Any]]:
    """`value` as a list of objects; absent (None) is an empty list, anything else is malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _Malformed
    return value


def _spans(value: Any) -> list[tuple[int, int]]:
    return [(_whole(s.get("offset")), _whole(s.get("offset")) + _whole(s.get("length"))) for s in _objects(value)]


def _word(page_number: int, n: int, word: dict[str, Any], handwritten: list[tuple[int, int]]) -> OcrWord:
    text = word.get("content")
    polygon = word.get("polygon")
    if not isinstance(text, str) or not isinstance(polygon, list) or len(polygon) != 8:
        raise _Malformed
    span = word.get("span")
    if span is None:
        offset = -1
    elif isinstance(span, dict):
        offset = _whole(span.get("offset"))
        _whole(span.get("length"))
    else:
        raise _Malformed
    return OcrWord(
        id=word_id(page_number, n),
        text=text,
        polygon=tuple(_number(v) for v in polygon),
        confidence=_number(word.get("confidence"), low=0.0, high=1.0),
        handwritten=any(start <= offset < end for start, end in handwritten),
    )


def _result(model: str, analysed: Any) -> OcrResult:
    """The service's `analyzeResult` as an `OcrResult`, or `OcrFailed("malformed_response")` (DP-01).

    The rule: any page or word that lacks the documented shape makes the whole response malformed. A page needs a
    whole `pageNumber` of 1 or more, used once; a word needs text, a polygon of exactly 8 finite numbers and a
    confidence from 0 to 1 (the `app.ocr_words` constraints). Page size and angle are optional numbers; `words`,
    `pages` and `styles` may be absent or empty. Nothing from the answer is put in the error or chained to it.
    """
    try:
        return _parse(model, analysed)
    except (_Malformed, TypeError, ValueError, KeyError, AttributeError, OverflowError):
        raise OcrFailed(_MALFORMED) from None


def _parse(model: str, analysed: Any) -> OcrResult:
    if not isinstance(analysed, dict):
        raise _Malformed
    handwritten: list[tuple[int, int]] = []
    styles = analysed.get("styles")
    for style in _objects(styles):
        if style.get("isHandwritten"):
            handwritten += _spans(style.get("spans"))
        else:
            _spans(style.get("spans"))
    seen: set[int] = set()
    pages: list[OcrPage] = []
    for page in _objects(analysed.get("pages")):
        number = _whole(page.get("pageNumber"), low=1)
        if number in seen:
            raise _Malformed
        seen.add(number)
        pages.append(
            OcrPage(
                number=number,
                width=_number(page.get("width") or 0.0, low=0.0),
                height=_number(page.get("height") or 0.0, low=0.0),
                angle=_number(page.get("angle") or 0.0),
                words=tuple(
                    _word(number, n, word, handwritten) for n, word in enumerate(_objects(page.get("words")), start=1)
                ),
            )
        )
    return OcrResult(model=model, api_version=API_VERSION, pages=tuple(sorted(pages, key=lambda p: p.number)))
