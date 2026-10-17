"""A page's words: from its own text layer when that is usable, otherwise from OCR (T-07a).

The text layer is unusable when the page is empty of text, when most of its characters are not
letters or digits, or when an image covers most of the page (a scan, perhaps under a typed header).
Words are kept exactly as read; the page's confidence and handwriting come from them, and a page
read with low confidence is marked unreadable for the expert (screen 10). Nothing is corrected.
"""

from __future__ import annotations

from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app.core.ai.ocr import OcrPage
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
    if source == "embedded":
        return Reading(confidence=1.0, handwriting=False, unreadable=False)
    words = page.words
    if not words:
        return Reading(confidence=0.0, handwriting=False, unreadable=True)
    confidence = sum(w.confidence for w in words) / len(words)
    handwriting = sum(w.handwritten for w in words) / len(words) >= HANDWRITTEN_SHARE
    return Reading(confidence=confidence, handwriting=handwriting, unreadable=confidence < UNREADABLE_BELOW)
