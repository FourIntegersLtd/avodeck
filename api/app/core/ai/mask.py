"""Rule 7 for pictures: paint identifiers black on a page image before any model sees it (T-10, ADR 0030).

`pii.spans` finds identifiers in the page's text (`extract.plain_text`: words joined by single spaces).
`mask_page` maps those character offsets back to the words they touch and fills each word's box solid black
on the picture. A `MaskedImage` can only be made here, as a `Stripped` only in `pii.py`, so the AI kit's
image path cannot be handed a raw picture.

Fail closed (`MaskUnsafe`, nothing is sent) when masking is uncertain:
- the page has no words, or a span touches no word;
- a word an identifier touches has no place on the picture (typed out by the expert, no polygon);
- OCR confidence is low, so an identifier may have been misread and left unmasked;
- the picture's shape does not match the frame the words are in (a turned page read in the wrong frame).

The picture is returned as PNG (lossless), so black stays exactly black. Words are in inches from the top
left of the page as displayed (`pipeline/words.turned`); the picture is rendered at `render.IMAGE_DPI`.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from app.core.ai.ocr import OcrWord
from app.core.ai.pii import Span

IMAGE_DPI = 150  # the page picture's resolution (features/pipeline/render.py)
MIN_CONFIDENCE = 0.6  # mean OCR confidence under which identifiers may have been misread
FRAME_TOLERANCE = 0.03  # share of the page's size the picture may differ by
DEFAULT_PAD_PX = 6

_SEAL = object()


class MaskUnsafe(Exception):
    """The picture cannot be masked safely. `reason` is a short code, never page content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class MaskedImage:
    """A page picture with identifiers painted out. Only `mask_page` makes one."""

    data: bytes = field(repr=False)
    sha256: str
    format: str
    masked_words: int
    boxes: tuple[tuple[int, int, int, int], ...]
    _seal: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("a MaskedImage is made by mask.mask_page only")


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _has_place(word: OcrWord) -> bool:
    poly = word.polygon
    return len(poly) >= 8 and any(poly) and (max(poly[0::2]) > min(poly[0::2])) and (max(poly[1::2]) > min(poly[1::2]))


def mask_page(
    image: bytes,
    words: Sequence[OcrWord],
    page_size: tuple[float, float],
    spans: Sequence[Span],
    *,
    pad_px: int = DEFAULT_PAD_PX,
    dpi: int = IMAGE_DPI,
    min_confidence: float = MIN_CONFIDENCE,
) -> MaskedImage:
    """`words` in the order `spans` were found over (their `plain_text`); `page_size` (width, height) in inches."""
    if not words:
        raise MaskUnsafe("no_words")
    if sum(w.confidence for w in words) / len(words) < min_confidence:
        raise MaskUnsafe("low_confidence")
    try:
        picture = Image.open(io.BytesIO(image))
        picture.load()
    except Exception:
        raise MaskUnsafe("unreadable_picture") from None
    picture = picture.convert("RGB")
    expected = (page_size[0] * dpi, page_size[1] * dpi)
    if any(abs(got - want) > want * FRAME_TOLERANCE + 2 for got, want in zip(picture.size, expected, strict=True)):
        raise MaskUnsafe("frame_uncertain")

    ranges: list[tuple[int, int]] = []
    pos = 0
    for w in words:
        n = len(_normalised(w.text))
        ranges.append((pos, pos + n))
        pos += n + 1

    touched: set[int] = set()
    for span in spans:
        hit = {i for i, (a, b) in enumerate(ranges) if a < span.end and span.start < b and b > a}
        if not hit:
            raise MaskUnsafe("span_without_word")
        touched |= hit
    if spans and not touched:
        raise MaskUnsafe("nothing_masked")

    draw = ImageDraw.Draw(picture)
    boxes: list[tuple[int, int, int, int]] = []
    for i in sorted(touched):
        word = words[i]
        if not _has_place(word):
            raise MaskUnsafe("no_place")
        xs, ys = word.polygon[0::2], word.polygon[1::2]
        box = (
            max(0, round(min(xs) * dpi) - pad_px),
            max(0, round(min(ys) * dpi) - pad_px),
            min(picture.width, round(max(xs) * dpi) + pad_px),
            min(picture.height, round(max(ys) * dpi) + pad_px),
        )
        draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(0, 0, 0))
        boxes.append(box)

    out = io.BytesIO()
    picture.save(out, format="PNG")
    data = out.getvalue()
    return MaskedImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        format="png",
        masked_words=len(touched),
        boxes=tuple(boxes),
        _seal=_SEAL,
    )
