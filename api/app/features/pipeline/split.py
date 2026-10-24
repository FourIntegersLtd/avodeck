"""Cutting a two-page scan (ADR 0019): one page's PDF becomes a two-page PDF, one half per page."""

from __future__ import annotations

import io

import pikepdf


def halves(page_pdf: bytes, axis: str, at: float, first: str) -> bytes:
    """Both halves as a two-page PDF, the `first` half first. `vertical` cuts down the page (left and
    right), `horizontal` across it (top and bottom); `at` is how far along the cut falls (0 to 1)."""
    with pikepdf.open(io.BytesIO(page_pdf)) as source:
        x0, y0, x1, y1 = (float(v) for v in source.pages[0].MediaBox)
        if axis == "vertical":
            cut = x0 + (x1 - x0) * at
            parts = {"left": [x0, y0, cut, y1], "right": [cut, y0, x1, y1]}
        else:
            cut = y1 - (y1 - y0) * at
            parts = {"top": [x0, cut, x1, y1], "bottom": [x0, y0, x1, cut]}
        order = [first, next(name for name in parts if name != first)]
        out = pikepdf.new()
        for name in order:
            out.pages.append(source.pages[0])
            page = out.pages[-1]
            page.MediaBox = pikepdf.Array(parts[name])
            page.CropBox = pikepdf.Array(parts[name])
        buffer = io.BytesIO()
        out.save(buffer)
        return buffer.getvalue()
