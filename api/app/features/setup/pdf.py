"""How many pages a PDF has, read from its page tree (screen 04). No OCR and no page content is read.

`None` when the file is not a PDF we can open; the screen then shows no page count. Counts are remembered
per file checksum (the same bytes always give the same count), so the screen asking again costs nothing.
"""

from __future__ import annotations

import io
from collections import OrderedDict

import pikepdf

_REMEMBERED: OrderedDict[str, int | None] = OrderedDict()
_LIMIT = 512


def page_count(data: bytes) -> int | None:
    if not data.startswith(b"%PDF"):
        return None
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except (pikepdf.PdfError, ValueError, RuntimeError):
        return None


def remembered(sha256: str) -> tuple[bool, int | None]:
    if sha256 in _REMEMBERED:
        _REMEMBERED.move_to_end(sha256)
        return True, _REMEMBERED[sha256]
    return False, None


def remember(sha256: str, count: int | None) -> None:
    _REMEMBERED[sha256] = count
    while len(_REMEMBERED) > _LIMIT:
        _REMEMBERED.popitem(last=False)
