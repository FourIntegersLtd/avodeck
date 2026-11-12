"""The OCR client interface and its recorded stand-in (architecture 7: Layout and Read models).

The live client is `azure_ocr.AzureOcr` (Azure AI Document Intelligence in the cell's region).
Word text is hidden from every repr so OCR output can never reach a log line by accident.

Word IDs are `p<page>w<n>`: the page's number within the analysed document (1 for a single page,
which is how the pipeline sends pages) and the word's place in reading order, from 1. Polygons are
8 numbers (x, y for each corner, clockwise from top left) in inches from the page's top-left corner,
as Document Intelligence gives them for PDFs. `struck` marks a word that is crossed out.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from psycopg import AsyncConnection

from app.core.ai.cell import check_endpoint
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings

OcrModel = Literal["layout", "read"]


@dataclass(frozen=True)
class OcrWord:
    id: str
    text: str = field(repr=False)
    polygon: tuple[float, ...]
    confidence: float
    handwritten: bool = False
    struck: bool = False


def word_id(page: int, n: int) -> str:
    """The ID of the nth word (from 1) in reading order on a page (from 1) of the analysed document."""
    return f"p{page}w{n}"


@dataclass(frozen=True)
class OcrPage:
    number: int
    width: float
    height: float
    words: tuple[OcrWord, ...] = field(repr=False)
    angle: float = 0.0  # degrees the content is turned clockwise, in (-180, 180]; polygons stay in the scanned frame


@dataclass(frozen=True)
class OcrResult:
    model: str
    pages: tuple[OcrPage, ...]
    api_version: str | None = None  # the service's API version that answered; None for a recorded answer (PV-18)


class OcrFailed(Exception):
    """The service refused or could not analyse the document. The message is a status or error code only."""


@runtime_checkable
class OcrClient(Protocol):
    endpoint: str

    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        """Read `document`. `locale` (a language tag from the pack, e.g. en-GB) tells the reader which language and
        script to expect; without it the reader guesses."""
        ...


async def record_call(
    conn: AsyncConnection,
    *,
    case_id: uuid.UUID,
    task: str,
    result: OcrResult,
    region: str,
    job_id: uuid.UUID | None = None,
) -> None:
    """One `ai_calls` row for an OCR call (PV-18): the model, the service's API version, the region and the job (so
    the job's `code_version` is recoverable). Metadata only: no page text, no token counts (rule 6)."""
    await conn.execute(
        "insert into app.ai_calls (case_id, person_id, task_id, model_id, region, prompt_version, input_tokens,"
        " output_tokens, job_id, api_version, outcome)"
        " values (%s, app.current_person_id(), %s, %s, %s, %s, 0, 0, %s, %s, 'ok')",
        (case_id, f"ocr.{task}", result.model, region, "n/a", job_id, result.api_version),
    )


def _result(model: str, data: dict[str, Any]) -> OcrResult:
    pages = tuple(
        OcrPage(
            number=int(p["number"]),
            width=float(p["width"]),
            height=float(p["height"]),
            angle=float(p.get("angle", 0.0)),
            words=tuple(
                OcrWord(
                    id=str(w["id"]),
                    text=str(w["text"]),
                    polygon=tuple(float(v) for v in w["polygon"]),
                    confidence=float(w["confidence"]),
                    handwritten=bool(w.get("handwritten", False)),
                    struck=bool(w.get("struck", False)),
                )
                for w in p["words"]
            ),
        )
        for p in data["pages"]
    )
    return OcrResult(model=model, pages=pages)


class RecordedOcr:
    kind = "ocr"

    def __init__(self, settings: AiSettings, store: FixtureStore, endpoint: str | None = None) -> None:
        self.store = store
        self.endpoint = check_endpoint(settings, endpoint or settings.ocr_endpoint)

    @staticmethod
    def request(document: bytes, model: str, locale: str | None = None) -> dict[str, str]:
        key = {"model": model, "document_sha256": hashlib.sha256(document).hexdigest()}
        if locale:  # only when given, so recordings made without one still match
            key["locale"] = locale
        return key

    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        return _result(model, self.store.lookup(self.kind, self.request(document, model, locale)))
