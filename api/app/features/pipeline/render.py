"""Page pictures: the page image (150 dpi JPEG) and a small thumbnail, from a one-page PDF."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image

IMAGE_DPI = 150
THUMBNAIL_WIDTH = 200
JPEG_QUALITY = 80
MAX_PICTURE_PIXELS = 25_000_000  # an A1 sheet at 150 dpi is 17 million; far under Pillow's 89 million limit


@dataclass(frozen=True)
class Pictures:
    image: bytes
    thumbnail: bytes


def picture_scale(width_pt: float, height_pt: float, dpi: int = IMAGE_DPI) -> float:
    """The scale to ask pdfium for: `dpi`, lowered (aspect ratio kept) if the picture would pass the pixel cap, so a
    page with a huge MediaBox cannot take the worker's memory. A normal page is unchanged."""
    scale = dpi / 72
    pixels = width_pt * height_pt * scale * scale
    if pixels <= MAX_PICTURE_PIXELS or pixels <= 0:
        return scale
    return scale * math.sqrt(MAX_PICTURE_PIXELS / pixels) * 0.999  # a hair under: pdfium rounds the sides up


def rasterise(page_pdf: bytes, rotation: int = 0) -> Image.Image:
    """The page as a picture. PDFium is not thread-safe, so this stays on the event loop's thread."""
    doc = pdfium.PdfDocument(page_pdf)
    try:
        page = doc[0]
        width, height = page.get_size()  # points; a quarter turn swaps the sides but not the pixel count
        bitmap = page.render(scale=picture_scale(width, height), rotation=rotation)
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
