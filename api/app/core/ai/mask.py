"""Rule 7 for pictures: paint identifiers black on a page image before any model sees it (T-10, ADR 0030).

`pii.spans` finds identifiers in the page's text (`extract.plain_text`: words joined by single spaces).
`mask_page` maps those character offsets back to the words they touch and fills each word's box solid black
on the picture. A `MaskedImage` can only be made here, as a `Stripped` only in `pii.py`, so the AI kit's
image path cannot be handed a raw picture.

Fail closed (`MaskUnsafe`, nothing is sent) when masking is uncertain:
- the page has no words, or a span touches no word;
- a word an identifier touches has no place on the picture (typed out by the expert, no polygon);
- OCR confidence is low, so an identifier may have been misread and left unmasked;
- the picture's shape does not match the frame the words are in (a turned page read in the wrong frame);
- a doubtful word (below `WORD_FLOOR`) is close to a name token or looks like a date, so OCR may have
  misread an identifier that no span could find.

A word the expert corrected sits on the first word of its run and the rest are blank, but the ink is on every
box: touching any word of a run paints the whole run.

The picture is returned as PNG (lossless), so black stays exactly black. Words are in inches from the top
left of the page as displayed (`pipeline/words.turned`); the picture is rendered at `render.IMAGE_DPI`.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image, ImageDraw

from app.core.ai.ocr import OcrWord
from app.core.ai.pii import Span

IMAGE_DPI = 150  # the page picture's resolution (features/pipeline/render.py)
MIN_CONFIDENCE = 0.35  # unreadable pages read low as a whole, and those are the pages T-10 is for
WORD_FLOOR = 0.5  # a word read below this may be an identifier OCR misspelt
FRAME_TOLERANCE = 0.03  # share of the page's size the picture may differ by
DEFAULT_PAD_PX = 6

_KEY = secrets.token_bytes(32)  # per process: a seal made here cannot be copied into another picture
_DATE_LIKE = re.compile(r"\d{1,4}\s*[/.\-]\s*\d{1,2}(\s*[/.\-]\s*\d{1,4})?|\d{6,}")


def _seal_for(sha256: str) -> str:
    return hmac.new(_KEY, sha256.encode(), "sha256").hexdigest()


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
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise TypeError("the picture does not match its hash")
        if not isinstance(self._seal, str) or not hmac.compare_digest(self._seal, _seal_for(self.sha256)):
            raise TypeError("a MaskedImage is made by mask.mask_page only")


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _letters(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, start=1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _name_tokens(names: Sequence[str]) -> set[str]:
    return {t for name in names for t in re.split(r"[^\w]+", name.lower()) if len(_letters(t)) >= 2}


def _doubtful(word: OcrWord, tokens: set[str]) -> bool:
    """A word read below the floor that nearly spells a name token or looks like a date."""
    if _DATE_LIKE.search(word.text):
        return True
    seen = _letters(word.text)
    return bool(seen) and any(
        _distance(seen, t) <= 2 or (seen[0] == t[0] and abs(len(seen) - len(t)) <= 1) for t in tokens
    )


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
    word_floor: float = WORD_FLOOR,
    runs: Sequence[Sequence[str]] = (),
    names: Sequence[str] = (),
) -> MaskedImage:
    """`words` in the order `spans` were found over (their `plain_text`); `page_size` (width, height) in inches.

    `runs` are the word ids of each correction the expert made (the reading sits on the first, the rest are
    blank): all are painted when any is touched. `names` are the identity's names, for the doubtful-word check.
    """
    if not words:
        raise MaskUnsafe("no_words")
    if sum(w.confidence for w in words) / len(words) < min_confidence:
        raise MaskUnsafe("low_confidence")
    try:
        opened = Image.open(io.BytesIO(image))
        opened.load()
    except Exception:
        raise MaskUnsafe("unreadable_picture") from None
    picture = opened.convert("RGB")
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
    index = {w.id: i for i, w in enumerate(words)}
    for run in runs:
        members = {index[i] for i in run if i in index}
        if members & touched:
            touched |= members
    tokens = _name_tokens(names)
    for i, w in enumerate(words):
        if i not in touched and w.confidence < word_floor and _doubtful(w, tokens):
            raise MaskUnsafe("doubtful_word")

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
    sha = hashlib.sha256(data).hexdigest()
    return MaskedImage(
        data=data,
        sha256=sha,
        format="png",
        masked_words=len(touched),
        boxes=tuple(boxes),
        _seal=_seal_for(sha),
    )


def turned_words(words: Sequence[OcrWord], width: float, height: float, rotation: int) -> list[OcrWord]:
    """Words found on the page as it was scanned, turned clockwise by `rotation` degrees to sit on the picture."""
    if rotation % 360 == 0:
        return list(words)
    out: list[OcrWord] = []
    for w in words:
        pts: list[float] = []
        for x, y in zip(w.polygon[0::2], w.polygon[1::2], strict=True):
            if rotation == 90:
                x, y = height - y, x
            elif rotation == 180:
                x, y = width - x, height - y
            elif rotation == 270:
                x, y = y, width - x
            pts += [x, y]
        out.append(replace(w, polygon=tuple(pts)))
    return out
