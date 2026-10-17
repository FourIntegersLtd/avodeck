"""The canonical page split and embedded-text reader (architecture 7 steps 1 and 2).

Every slice that needs one page of a document as its own PDF calls `split_pages`, so the bytes,
and so the SHA-256 that keys OCR recordings and duplicate checks, are the same everywhere.
Born-digital pages give their words from the text layer with `embedded_words`, in the same shape
as OCR output: IDs `p1w<n>` in reading order, polygons in inches from the top-left, confidence 1.
"""

from __future__ import annotations

import io

import pikepdf
import pypdfium2 as pdfium

from app.core.ai.ocr import OcrPage, OcrWord, word_id

POINTS_PER_INCH = 72.0


def _save(pdf: pikepdf.Pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(
        out,
        deterministic_id=True,
        linearize=False,
        compress_streams=True,
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
    )
    return out.getvalue()


def split_pages(document: bytes) -> list[bytes]:
    """One single-page PDF per page, in order, with no metadata; the same bytes every time."""
    out: list[bytes] = []
    with pikepdf.open(io.BytesIO(document)) as source:
        for page in source.pages:
            single = pikepdf.new()
            single.pages.append(page)
            single.docinfo = single.make_indirect(pikepdf.Dictionary())
            if pikepdf.Name.Metadata in single.Root:
                del single.Root.Metadata
            out.append(_save(single))
    return out


def embedded_words(page_pdf: bytes) -> OcrPage:
    """The words of the first page's text layer (none for an image-only page)."""
    doc = pdfium.PdfDocument(page_pdf)
    try:
        page = doc[0]
        width, height = page.get_size()
        textpage = page.get_textpage()
        boxes: list[tuple[str, list[float]]] = []
        current, box = "", [0.0, 0.0, 0.0, 0.0]
        for i in range(textpage.count_chars()):
            ch = textpage.get_text_range(i, 1)
            if not ch or ch.isspace():
                if current:
                    boxes.append((current, box))
                current = ""
                continue
            left, bottom, right, top = textpage.get_charbox(i)
            if not current:
                box = [left, bottom, right, top]
            else:
                box = [min(box[0], left), min(box[1], bottom), max(box[2], right), max(box[3], top)]
            current += ch
        if current:
            boxes.append((current, box))
        textpage.close()
        page.close()
    finally:
        doc.close()
    words = []
    for n, (text, (left, bottom, right, top)) in enumerate(boxes, start=1):
        x0, x1 = left / POINTS_PER_INCH, right / POINTS_PER_INCH
        y0, y1 = (height - top) / POINTS_PER_INCH, (height - bottom) / POINTS_PER_INCH
        words.append(OcrWord(id=word_id(1, n), text=text, polygon=(x0, y0, x1, y0, x1, y1, x0, y1), confidence=1.0))
    return OcrPage(number=1, width=width / POINTS_PER_INCH, height=height / POINTS_PER_INCH, words=tuple(words))
