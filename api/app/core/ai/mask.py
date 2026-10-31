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
from app.core.ai.pii import MONTHS, Span
from app.core.packs import MergedPack

IMAGE_DPI = 150  # the page picture's resolution (features/pipeline/render.py)
MIN_CONFIDENCE = 0.35  # unreadable pages read low as a whole, and those are the pages T-10 is for
WORD_FLOOR = 0.5  # a word read below this may be an identifier OCR misspelt
FRAME_TOLERANCE = 0.03  # share of the page's size the picture may differ by
DEFAULT_PAD_PX = 6

_KEY = secrets.token_bytes(32)  # per process: a seal made here cannot be copied into another picture
_DATE_LIKE = re.compile(r"\d{1,4}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}|\d{6,}")
_DIGIT_LIKE = set("0123456789OolI|")  # letters handwriting is read as in place of a digit
_AS_DIGITS = str.maketrans("OolI|", "00111")


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
        if not is_genuine(self):
            raise TypeError("a MaskedImage is made by mask.mask_page only, and its picture is the one it sealed")


def is_genuine(image: MaskedImage) -> bool:
    """True when the picture is the one `mask_page` sealed: its hash and its seal both match."""
    return (
        hashlib.sha256(image.data).hexdigest() == image.sha256
        and isinstance(image._seal, str)
        and hmac.compare_digest(image._seal, _seal_for(image.sha256))
    )


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
    return {t for name in names for part in re.split(r"[^\w]+", name.lower()) if len(t := _letters(part)) >= 2}


def _close_to_name(seen: str, tokens: set[str]) -> bool:
    """Short tokens (under 3 letters) match exactly; 3 to 5 letters allow one change, longer ones two. From 4
    letters, the same first letter and a length within 1 also counts when the changes are at most half."""
    for t in tokens:
        if len(t) < 3:
            if seen == t:
                return True
            continue
        gap = _distance(seen, t)
        if gap <= (1 if len(t) <= 5 else 2):
            return True
        if len(t) >= 4 and seen[0] == t[0] and abs(len(seen) - len(t)) <= 1 and gap <= len(t) // 2:
            return True
    return False


def _digitish(text: str) -> bool:
    """Mostly digits (or letters read for digits, like O, l and I), at least three of them, one a real digit."""
    chars = [c for c in text if c.isalnum() or c == "|"]
    like = sum(1 for c in chars if c in _DIGIT_LIKE)
    return bool(chars) and any(c.isdigit() for c in chars) and like >= 3 and like / len(chars) > 0.6


def _doubtful(word: OcrWord, tokens: set[str]) -> bool:
    """A word read below the floor that nearly spells a name token, looks like a date or is mostly digits."""
    if _DATE_LIKE.search(word.text.translate(_AS_DIGITS)) or _digitish(word.text):
        return True
    seen = _letters(word.text)
    return bool(seen) and _close_to_name(seen, tokens)


def _doubtful_digit_runs(words: Sequence[OcrWord], doubt: list[int], pack: MergedPack | None) -> bool:
    """Neighbouring doubtful words with digits that together could be an identifier number or a date."""
    i = 0
    while i < len(doubt):
        j = i
        while j + 1 < len(doubt) and doubt[j + 1] == doubt[j] + 1:
            j += 1
        text = " ".join(words[k].text for k in doubt[i : j + 1]).translate(_AS_DIGITS)
        if sum(c.isdigit() for c in text) >= 6:
            return True
        if pack is not None:
            find = pack.identifier_recognisers()
            if find(text) or find("".join(c for c in text if c.isdigit())):
                return True
        i = j + 1
    return False


def _misread_month(words: Sequence[OcrWord], i: int, months: Sequence[str]) -> bool:
    """A month name read with one letter wrong (or its short form) with digits within two words of it."""
    seen = _letters(words[i].text)
    if len(seen) < 3:
        return False
    near = [w for k, w in enumerate(words) if k != i and abs(k - i) <= 2]
    if not any(c.isdigit() for w in near for c in w.text):
        return False
    return any((len(seen) >= 4 and _distance(seen, m) <= 1) or (len(seen) == 3 and seen == m[:3]) for m in months)


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
    pack: MergedPack | None = None,
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
    months = MONTHS.get(pack.language, ()) if pack is not None else ()
    doubt = [i for i, w in enumerate(words) if i not in touched and w.confidence < word_floor]
    if any(_doubtful(words[i], tokens) or _misread_month(words, i, months) for i in doubt) or _doubtful_digit_runs(
        words, [i for i in doubt if any(c.isdigit() or c in _DIGIT_LIKE for c in words[i].text)], pack
    ):
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
