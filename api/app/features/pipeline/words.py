"""A page's words: from its own text layer when that is usable, otherwise from OCR (T-07a).

The text layer is unusable when the page is empty of text, when most of its characters are not
letters or digits, or when an image covers most of the page (a scan, perhaps under a typed header).
Words are kept exactly as read; the page's confidence and handwriting come from them, and a page
read with low confidence is marked unreadable for the expert (screen 10). Nothing is corrected.

A page the expert turned is read as it was scanned and its words are turned with it (`turned`), so
they sit where the turned picture shows them. A page the expert typed out takes its words from that
text (`typed`), with no place on the picture.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app.core.ai.ocr import OcrPage, OcrWord
from app.core.pdf import embedded_words

IMAGE_COVERS = 0.5  # share of the page an image must cover to count as a scan
USABLE_CHARACTERS = 0.6  # share of a text layer's characters that must be letters or digits
UNREADABLE_BELOW = 0.6  # mean word confidence under which the page goes to screen 10
HANDWRITTEN_SHARE = 0.5  # share of handwritten words that marks the page as handwriting


def image_cover(page_pdf: bytes) -> float:
    doc = pdfium.PdfDocument(page_pdf)
    try:
        page = doc[0]
        width, height = page.get_size()
        cover = 0.0
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=8):
            left, bottom, right, top = obj.get_bounds()
            cover = max(cover, (right - left) * (top - bottom) / (width * height))
        page.close()
        return cover
    finally:
        doc.close()


def usable_text_layer(page_pdf: bytes) -> OcrPage | None:
    """The page's embedded words, or None when the page must go to OCR."""
    if image_cover(page_pdf) > IMAGE_COVERS:
        return None
    page = embedded_words(page_pdf)
    characters = "".join(w.text for w in page.words)
    if not characters or sum(c.isalnum() for c in characters) / len(characters) < USABLE_CHARACTERS:
        return None
    return page


@dataclass(frozen=True)
class Reading:
    confidence: float
    handwriting: bool
    unreadable: bool


def assess(page: OcrPage, source: str) -> Reading:
    if source in ("embedded", "expert"):
        return Reading(confidence=1.0, handwriting=False, unreadable=False)
    words = page.words
    if not words:
        return Reading(confidence=0.0, handwriting=False, unreadable=True)
    confidence = sum(w.confidence for w in words) / len(words)
    handwriting = sum(w.handwritten for w in words) / len(words) >= HANDWRITTEN_SHARE
    return Reading(confidence=confidence, handwriting=handwriting, unreadable=confidence < UNREADABLE_BELOW)


def _turn(polygon: tuple[float, ...], rotation: int, width: float, height: float) -> tuple[float, ...]:
    """One polygon (x down the page from the left, y down from the top) turned clockwise."""
    out: list[float] = []
    for x, y in zip(polygon[0::2], polygon[1::2], strict=True):
        if rotation == 90:
            x, y = height - y, x
        elif rotation == 180:
            x, y = width - x, height - y
        elif rotation == 270:
            x, y = y, width - x
        out += [x, y]
    return tuple(out)


def turned(page: OcrPage, rotation: int) -> OcrPage:
    """The page's words as they sit on the picture turned clockwise by `rotation` degrees."""
    if rotation % 360 == 0:
        return page
    words = tuple(replace(w, polygon=_turn(w.polygon, rotation, page.width, page.height)) for w in page.words)
    width, height = (page.height, page.width) if rotation in (90, 270) else (page.width, page.height)
    return OcrPage(page.number, width, height, words)


EXPERT_ID = "e{n}"


def typed(text: str) -> OcrPage:
    """The expert's typed-out text as words `e1`, `e2`, ... (no place on the picture, full confidence)."""
    words = tuple(OcrWord(EXPERT_ID.format(n=n), word, (0.0,) * 8, 1.0) for n, word in enumerate(text.split(), start=1))
    return OcrPage(1, 1.0, 1.0, words)
