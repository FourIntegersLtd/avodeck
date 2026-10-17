"""Duplicate pages (architecture 7, failure mode F13, planted item 1).

A page is a copy of an earlier page when it shows the same typed content: the same words in the
same order once case and edge punctuation are set aside, with the same numbers and dates. Words
written by hand or crossed out are left out, so a copy that was annotated later is still a copy.
A word may differ only as a misread does (a close spelling with no digits); a changed month, name
or value, or a line added or taken away, means the pages are not copies. Near copies printed in
another layout (a lab's own print of a result) therefore are never set aside.

- `signature(...)`: what is kept for each page: a perceptual hash of its thumbnail and a MinHash of
  its typed words, so likely pairs are found without comparing every page with every other.
- `likely_pairs(...)`: pairs whose MinHash and picture are both close.
- `same_content(a, b, pack)`: the final word-by-word comparison.
- `originals(...)`: for each new page, the earliest page it copies (never itself a copy).
"""

from __future__ import annotations

import difflib
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import imagehash
from datasketch import MinHash, MinHashLSH
from PIL import Image
from rapidfuzz import fuzz

from app.core.packs import MergedPack
from app.features.pipeline import dates

NUM_PERM = 128
SCHEME = "affine32"
LIKELY = 0.8  # MinHash similarity at which two pages are compared word by word
PICTURE_DISTANCE = 16  # most bits two copies' picture hashes may differ by (of 64)
MISREAD = 80  # least spelling similarity (0-100) for one word read two ways
MIN_WORDS = 10  # pages with fewer typed words are never set aside
NOTE_WORDS = 6  # most handwritten words one copy may have that the other lacks (a filing note)
_EDGES = re.compile(r"^\W+|\W+$")
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class Word:
    text: str
    handwritten: bool


def tokens(words: Iterable[tuple[str, bool, bool]]) -> list[Word]:
    """The page's words (`(text, handwritten, struck)` in reading order) as compared: crossed-out
    words left out, case and edge punctuation set aside."""
    out = []
    for text, handwritten, struck in words:
        word = _EDGES.sub("", text).casefold()
        if word and not struck:
            out.append(Word(word, handwritten))
    return out


def picture_hash(thumbnail: bytes) -> int:
    """A 64-bit perceptual hash of the page's thumbnail, as a signed integer for storage."""
    with Image.open(io.BytesIO(thumbnail)) as img:
        value = int(str(imagehash.phash(img.convert("RGB"))), 16)
    return value - (1 << 64) if value >= 1 << 63 else value


def word_hash(page: Sequence[Word]) -> list[int]:
    words = [w.text for w in page]
    sketch = MinHash(num_perm=NUM_PERM, scheme=SCHEME)
    for shingle in {" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2))}:
        sketch.update(shingle.encode())
    return [int(v) for v in sketch.hashvalues]


def _distance(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


@dataclass(frozen=True)
class Signature:
    page_id: object
    page_no: int
    picture: int
    words: list[int]
    word_count: int  # typed words


def _sketch(values: Sequence[int]) -> MinHash:
    return MinHash(num_perm=NUM_PERM, scheme=SCHEME, hashvalues=values)


def likely_pairs(signatures: Sequence[Signature], new: set[object]) -> list[tuple[Signature, Signature]]:
    """Pairs (earlier, later) with at least one new page whose word and picture hashes are close."""
    usable = [s for s in signatures if s.word_count >= MIN_WORDS]
    index = MinHashLSH(threshold=LIKELY, num_perm=NUM_PERM)
    sketches = {}
    for s in usable:
        sketches[s.page_id] = _sketch(s.words)
        index.insert(str(s.page_id), sketches[s.page_id])
    by_id = {str(s.page_id): s for s in usable}
    pairs: dict[tuple[int, int], tuple[Signature, Signature]] = {}
    for s in usable:
        if s.page_id not in new:
            continue
        for key in index.query(sketches[s.page_id]):
            other = by_id[key]
            if other.page_id == s.page_id:
                continue
            if sketches[s.page_id].jaccard(sketches[other.page_id]) < LIKELY:
                continue
            if _distance(s.picture, other.picture) > PICTURE_DISTANCE:
                continue
            first, second = sorted((s, other), key=lambda x: x.page_no)
            pairs[(first.page_no, second.page_no)] = (first, second)
    return [pairs[k] for k in sorted(pairs)]


def _typed(page: Sequence[Word]) -> list[str]:
    return [w.text for w in page if not w.handwritten]


def _printed(words: Sequence[str], pack: MergedPack) -> list[object]:
    return list(dates.printed_dates(dates.find([(str(n), w) for n, w in enumerate(words)], pack)))


def same_content(a: Sequence[Word], b: Sequence[Word], pack: MergedPack) -> bool:
    """Whether two pages show the same content (see the module notes)."""
    typed_a, typed_b = _typed(a), _typed(b)
    if len(typed_a) < MIN_WORDS or len(typed_b) < MIN_WORDS:
        return False
    if [w for w in typed_a if _DIGIT.search(w)] != [w for w in typed_b if _DIGIT.search(w)]:
        return False
    if _printed(typed_a, pack) != _printed(typed_b, pack):
        return False
    words_a, words_b = [w.text for w in a], [w.text for w in b]
    added = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, words_a, words_b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op in ("insert", "delete"):
            extra = a[i1:i2] or b[j1:j2]
            if not all(w.handwritten for w in extra):
                return False
            added += len(extra)
            continue
        if i2 - i1 != j2 - j1:
            return False
        for x, y in zip(words_a[i1:i2], words_b[j1:j2], strict=True):
            if _DIGIT.search(x) or _DIGIT.search(y) or fuzz.ratio(x, y) < MISREAD:
                return False
    return added <= NOTE_WORDS


def originals(
    pairs: Iterable[tuple[Any, Any]],
    page_no: Mapping[Any, int],
    copy_of: Mapping[Any, Any],
    new: set[Any],
    kept: set[Any],
) -> dict[Any, Any]:
    """Each new page that copies an earlier page, with the earliest such page that is not a copy."""
    matches: dict[object, list[object]] = {}
    for first, second in pairs:
        matches.setdefault(second, []).append(first)
    found: dict[object, object] = {}
    for page in sorted(new, key=lambda p: page_no[p]):
        if page in kept or copy_of.get(page) is not None:
            continue
        roots = set()
        for earlier in matches.get(page, []):
            root = found.get(earlier) or copy_of.get(earlier) or earlier
            roots.add(root)
        if roots:
            found[page] = min(roots, key=lambda p: page_no[p])
    return found
