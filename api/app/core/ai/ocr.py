"""The OCR client interface and its recorded stand-in (architecture 7: Layout and Read models).

The real client (Azure AI Document Intelligence in the cell's region) arrives with the cloud cell.
Word text is hidden from every repr so OCR output can never reach a log line by accident.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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


@dataclass(frozen=True)
class OcrPage:
    number: int
    width: float
    height: float
    words: tuple[OcrWord, ...] = field(repr=False)


@dataclass(frozen=True)
class OcrResult:
    model: str
    pages: tuple[OcrPage, ...]


class OcrClient(Protocol):
    endpoint: str

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult: ...


def _result(model: str, data: dict[str, Any]) -> OcrResult:
    pages = tuple(
        OcrPage(
            number=int(p["number"]),
            width=float(p["width"]),
            height=float(p["height"]),
            words=tuple(
                OcrWord(
                    id=str(w["id"]),
                    text=str(w["text"]),
                    polygon=tuple(float(v) for v in w["polygon"]),
                    confidence=float(w["confidence"]),
                    handwritten=bool(w.get("handwritten", False)),
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
    def request(document: bytes, model: str) -> dict[str, str]:
        return {"model": model, "document_sha256": hashlib.sha256(document).hexdigest()}

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        return _result(model, self.store.lookup(self.kind, self.request(document, model)))
