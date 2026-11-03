"""Page pictures: the page image (150 dpi JPEG) and a small thumbnail, from a one-page PDF."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image

IMAGE_DPI = 150
THUMBNAIL_WIDTH = 200
JPEG_QUALITY = 80


@dataclass(frozen=True)
class Pictures:
    image: bytes
    thumbnail: bytes


def rasterise(page_pdf: bytes, rotation: int = 0) -> Image.Image:
    """The page as a picture. PDFium is not thread-safe, so this stays on the event loop's thread."""
    doc = pdfium.PdfDocument(page_pdf)
    try:
        page = doc[0]
        bitmap = page.render(scale=IMAGE_DPI / 72, rotation=rotation)
        image: Image.Image = bitmap.to_pil().convert("RGB")
        page.close()
        return image
    finally:
        doc.close()


def encode(image: Image.Image) -> Pictures:
    """JPEG-encodes the page image and its thumbnail: pure CPU, safe to run in a worker thread."""
    full = io.BytesIO()
    image.save(full, format="JPEG", quality=JPEG_QUALITY)
    small = image.copy()
    small.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2))
    thumb = io.BytesIO()
    small.save(thumb, format="JPEG", quality=JPEG_QUALITY)
    return Pictures(image=full.getvalue(), thumbnail=thumb.getvalue())


def render(page_pdf: bytes, rotation: int = 0) -> Pictures:
    return encode(rasterise(page_pdf, rotation))
