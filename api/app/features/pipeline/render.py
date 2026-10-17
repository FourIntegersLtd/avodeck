"""Page pictures: the page image (150 dpi JPEG) and a small thumbnail, from a one-page PDF."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pypdfium2 as pdfium

IMAGE_DPI = 150
THUMBNAIL_WIDTH = 200
JPEG_QUALITY = 80


@dataclass(frozen=True)
class Pictures:
    image: bytes
    thumbnail: bytes


def render(page_pdf: bytes, rotation: int = 0) -> Pictures:
    doc = pdfium.PdfDocument(page_pdf)
    try:
        page = doc[0]
        bitmap = page.render(scale=IMAGE_DPI / 72, rotation=rotation)
        image = bitmap.to_pil().convert("RGB")
        page.close()
    finally:
        doc.close()
    full = io.BytesIO()
    image.save(full, format="JPEG", quality=JPEG_QUALITY)
    small = image.copy()
    small.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2))
    thumb = io.BytesIO()
    small.save(thumb, format="JPEG", quality=JPEG_QUALITY)
    return Pictures(image=full.getvalue(), thumbnail=thumb.getvalue())
