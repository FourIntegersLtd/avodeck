"""The live OCR client: Azure AI Document Intelligence v4 (REST `2024-11-30`) in the cell's region.

`analyse` sends the document to the prebuilt Layout or Read model, polls the `Operation-Location`
the service returns (only if it is on the cell's allow-list and the same host), and maps the result
to `OcrResult`: word IDs `p<page>w<n>` in the service's reading order, 8-number polygons in the
page's unit (inches for PDF), confidence, and handwriting from the result's `styles` spans. The
service does not report crossed-out text, so `struck` stays False.

Busy (429, 5xx) or unreachable: growing waits honouring `Retry-After`, then `ServiceBusy`. Refused
(other 4xx) or a failed analysis: `OcrFailed` with a status or error code only. Logs carry counts,
durations, status codes and request IDs, never words (rule 6).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.ai.bedrock import ServiceBusy
from app.core.ai.cell import EndpointNotAllowed, check_endpoint
from app.core.ai.ocr import OcrFailed, OcrModel, OcrPage, OcrResult, OcrWord, word_id
from app.core.config import AiSettings

logger = logging.getLogger("app.ai.ocr")

API_VERSION = "2024-11-30"
MODEL_IDS: dict[str, str] = {"layout": "prebuilt-layout", "read": "prebuilt-read"}
_BUSY = frozenset({408, 429, 500, 502, 503, 504})
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(float(response.headers["Retry-After"]), 0.0)
    except (KeyError, ValueError):
        return None


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

    def __repr__(self) -> str:
        return f"AzureOcr(endpoint={self.endpoint!r})"

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        started = time.monotonic()
        model_id = MODEL_IDS[model]
        url = f"{self.endpoint.rstrip('/')}/documentintelligence/documentModels/{model_id}:analyze"
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as http:
            submitted = await self._send(
                http,
                "POST",
                url,
                params={"api-version": API_VERSION},
                content=document,
                headers={"Content-Type": "application/octet-stream"},
            )
            location = submitted.headers.get("Operation-Location")
            if submitted.status_code != 202 or not location:
                raise OcrFailed(f"http_{submitted.status_code}")
            self._check_location(location)
            data, polls = await self._poll(http, location, _retry_after(submitted))
        result = _result(model, data.get("analyzeResult") or {})
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
            data = response.json()
            status = data.get("status")
            if status == "succeeded":
                return data, polls
            if status in ("failed", "canceled"):
                code = str((data.get("error") or {}).get("code") or status)
                logger.error("ocr analysis %s (code %s)", status, code if _CODE.match(code) else "-")
                raise OcrFailed(code if _CODE.match(code) else status)
            wait = _retry_after(response)

    async def _send(self, http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Ocp-Apim-Subscription-Key": self._key, **kwargs.pop("headers", {})}
        for attempt in range(self._retries + 1):
            retry_after: float | None = None
            try:
                response = await http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                status: str = type(exc).__name__
            else:
                if response.status_code not in _BUSY:
                    if response.status_code >= 400:
                        logger.warning("ocr %s refused with status %d", method, response.status_code)
                        raise OcrFailed(f"http_{response.status_code}")
                    return response
                status, retry_after = str(response.status_code), _retry_after(response)
            if attempt == self._retries:
                logger.error("ocr service still busy (%s) after %d attempts", status, attempt + 1)
                raise ServiceBusy(retry_after)
            pause = max(self._base_delay * 2**attempt, retry_after or 0.0)
            logger.warning("ocr service busy (%s); waiting %.0f s (attempt %d)", status, pause, attempt + 1)
            await self._sleep(pause)
        raise AssertionError("unreachable")  # pragma: no cover


def _result(model: str, analysed: dict[str, Any]) -> OcrResult:
    handwritten = [
        (int(span["offset"]), int(span["offset"]) + int(span["length"]))
        for style in analysed.get("styles") or ()
        if style.get("isHandwritten")
        for span in style.get("spans") or ()
    ]

    def is_handwritten(word: dict[str, Any]) -> bool:
        offset = int((word.get("span") or {}).get("offset", -1))
        return any(start <= offset < end for start, end in handwritten)

    pages = sorted(analysed.get("pages") or (), key=lambda p: int(p["pageNumber"]))
    return OcrResult(
        model=model,
        pages=tuple(
            OcrPage(
                number=int(page["pageNumber"]),
                width=float(page.get("width") or 0.0),
                height=float(page.get("height") or 0.0),
                words=tuple(
                    OcrWord(
                        id=word_id(int(page["pageNumber"]), n),
                        text=str(word["content"]),
                        polygon=tuple(float(v) for v in word.get("polygon") or ()),
                        confidence=float(word.get("confidence") or 0.0),
                        handwritten=is_handwritten(word),
                    )
                    for n, word in enumerate(page.get("words") or (), start=1)
                ),
            )
            for page in pages
        ),
    )
