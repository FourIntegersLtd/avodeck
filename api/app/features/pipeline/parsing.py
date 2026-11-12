"""The parser calls the pipeline runs in killable child processes (audit 13, DP-09; `app.core.sandbox`).

Every function here is importable at the top level, takes only picklable arguments (paths, bytes, numbers,
dataclasses) and returns only picklable results, because it runs in a child of the sandbox pool that shares nothing
with the worker. They are the only places the pipeline hands a stranger's bytes to pikepdf, PDFium or Pillow.
The service looks them up on this module at call time (`sandbox.run(parsing.cut_pages, ...)`), so a test can swap
one for a stand-in that hangs or exits. Nothing here logs; an error carries a class and no text (rule 6).
"""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
from PIL import Image

from app.core.ai.ocr import OcrPage
from app.core.config import document_limits
from app.core.pdf import cut_page
from app.features.pipeline import convert, duplicates, render, split
from app.features.pipeline.render import Pictures
from app.features.pipeline.words import usable_text_layer


class TooManyPages(Exception):
    """A document has more pages than `MAX_DOCUMENT_PAGES` (or would take the bundle past `MAX_BUNDLE_PAGES`)."""


def checked_pages(source: Path | str | bytes, max_pages: int | None = None) -> int:
    """The page count of a PDF, after cutting every page once and dropping it, so a damaged page stops the
    document here (F2), as it always did, not part-way through the pictures.

    The reader is told not to repair (`attempt_recovery=False`): a cut-off file, or one whose cross-reference
    table is wrong, raises `PdfError` instead of being silently rebuilt with pages lost (V4d). A PDF with no
    pages raises too (V4e), so either way the file is listed as damaged and the expert is told. A PDF over
    `max_pages` (default `MAX_DOCUMENT_PAGES`) raises `TooManyPages` on the count alone, before any page is cut."""
    cap = document_limits().max_document_pages if max_pages is None else max_pages
    with pikepdf.open(io.BytesIO(source) if isinstance(source, bytes) else source, attempt_recovery=False) as pdf:
        count = len(pdf.pages)
        if count == 0:
            raise pikepdf.PdfError("no pages")
        if count > cap:
            raise TooManyPages
        for index in range(count):
            cut_page(pdf, index)
        return count


def count_pages(source: str | bytes, max_pages: int) -> int:
    return checked_pages(source, max_pages)


def cut_pages(path: str, indexes: list[int]) -> list[bytes]:
    """Pages `indexes` (0-based) of the PDF at `path`, each as its own PDF, with the reader that counted them."""
    with pikepdf.open(path, attempt_recovery=False) as pdf:
        return [cut_page(pdf, index) for index in indexes]


def halves(page_pdf: bytes, axis: str, at: float, first: str) -> bytes:
    """The two-page cut of one stored page (the expert's split request), made in a sandbox child."""
    return split.halves(page_pdf, axis, at, first)


def picture_hash(thumbnail: bytes) -> int:
    """The duplicate check's perceptual hash of a stored thumbnail (a decode of bytes drawn from the document)."""
    return duplicates.picture_hash(thumbnail)


def unpack(data: bytes, filename: str, limits: convert.Limits) -> convert.Unpacked:
    return convert.unpack(data, filename, limits)


def text_layer(page_pdf: bytes) -> OcrPage | None:
    return usable_text_layer(page_pdf)


def pictures(page_pdf: bytes, rotation: int) -> Pictures:
    return render.render(page_pdf, rotation)


# --- stand-ins for a page that could not be cut or pictured: made here from nothing, never from the stranger's file


def blank_page() -> bytes:
    """A one-page PDF with nothing on it."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def blank_pictures() -> Pictures:
    return render.encode(Image.new("RGB", (794, 1123), "white"))
