"""Duplicate pages (architecture 7, failure mode F13, planted item 1).

A page is a copy of an earlier page when it shows the same typed content: the same words in the
same order once case and edge punctuation are set aside, with the same numbers and dates. Words
written by hand or crossed out are left out, so a copy that was annotated later is still a copy.
A word may differ only as a misread does (a close spelling with no digits); a changed month, name
or value, or a line added or taken away, means the pages are not copies. Near copies printed in
another layout (a lab's own print of a result) therefore are never set aside, unless their words are
nearly identical (Jaccard 0.95 or more), when the picture no longer has to agree. A number that differs
only in its separators (12/05/2024 and 12.05.2024) is the same number; a changed digit is not.

A re-scan can also keep a boilerplate header or footer on one scan and lose it on the other. A header or footer is a
run of three to twelve typed words with no digit that starts or ends at least three other pages of the bundle whose
content differs (`edge_runs` counts each distinct page once, so one page filed many times is not a bundle, and
`same_content` leaves the page under test out of the count). A run seen on fewer pages, or one holding a figure
(a dose, a date, a result), is a real addition. A line that is only moved is never set aside: where a line sits
can be what it means (the list it is under, the dated entry it belongs to).

- `signature(...)`: what is kept for each page: a perceptual hash of its thumbnail and a MinHash of
  its typed words, so likely pairs are found without comparing every page with every other.
- `likely_pairs(...)`: pairs whose MinHash and picture are both close.
- `same_content(a, b, pack)`: the final word-by-word comparison.
- `edge_runs(pages)`: how many distinct pages start or end with each run of words, to tell a repeated header or footer.
- `originals(...)`: for each new page, the earliest page it copies (never itself a copy).
"""

from __future__ import annotations

import difflib
import io
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import imagehash
from PIL import Image
from rapidfuzz import fuzz

if TYPE_CHECKING:  # datasketch pulls in scipy (0.6 s): imported where it is used, so a sandbox child does not pay
    from datasketch import MinHash

from app.core.packs import MergedPack
from app.features.pipeline import dates

NUM_PERM = 128
SCHEME = "affine32"
LIKELY = 0.6  # MinHash similarity at which two pages are compared word by word
PICTURE_DISTANCE = 16  # most bits two copies' picture hashes may differ by (of 64)
IDENTICAL = 0.95  # MinHash similarity at which the picture is not asked: a re-scan can change the page's shape
MISREAD = 80  # least spelling similarity (0-100) for one word read two ways
MIN_WORDS = 10  # pages with fewer typed words are never set aside
NOTE_WORDS = 6  # most handwritten words one copy may have that the other lacks (a filing note)
EDGE_WORDS = 3  # fewest and most words in a repeated header or footer
EDGE_MAX = 12
EDGE_PAGES = 3  # other pages of different content that must start or end with the same run
_EDGES = re.compile(r"^\W+|\W+$")
_DIGIT = re.compile(r"\d")
# Between two digits, OCR varies the separator of a date or long identifier (`12/05/2024`, `12.05.2024`) or splits
# a number in two (`999 447`). A comma stays distinct from a point (1,500 is not 1.500), and a separator at an
# edge is kept. A number of two groups is set aside only when both groups have two digits or more: `1/2` is
# not `1.2` and `10 5` is not `10.5`. A run of three groups or more is always a date or an identifier.
_SEPARATOR = r"[/.:\u2010-\u2015 -]+"
_GROUPS = re.compile(rf"\d+(?:{_SEPARATOR}\d+)+")
_SPLIT = re.compile(_SEPARATOR)


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
    from datasketch import MinHash

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
    from datasketch import MinHash

    return MinHash(num_perm=NUM_PERM, scheme=SCHEME, hashvalues=values)


def likely_pairs(signatures: Sequence[Signature], new: set[object]) -> list[tuple[Signature, Signature]]:
    """Pairs (earlier, later) with at least one new page whose word and picture hashes are close."""
    from datasketch import MinHashLSH

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
            similar = sketches[s.page_id].jaccard(sketches[other.page_id])
            if similar < LIKELY:
                continue
            if similar < IDENTICAL and _distance(s.picture, other.picture) > PICTURE_DISTANCE:
                continue
            first, second = sorted((s, other), key=lambda x: x.page_no)
            pairs[(first.page_no, second.page_no)] = (first, second)
    return [pairs[k] for k in sorted(pairs)]


def _number(word: str) -> str:
    """Text with digits as its numbers: one separator between the groups of a date, a long identifier or a
    number with groups of two digits or more is as good as another (a single-digit group keeps its own)."""
    return _GROUPS.sub(_joined, word)


def _joined(run: re.Match[str]) -> str:
    groups = _SPLIT.split(run.group())
    if len(groups) >= 3 or all(len(g) >= 2 for g in groups):
        return ".".join(groups)
    return run.group()


def _typed(page: Sequence[Word]) -> list[str]:
    return [w.text for w in page if not w.handwritten]


def _printed(words: Sequence[str], pack: MergedPack) -> list[object]:
    return list(dates.printed_dates(dates.find([(str(n), w) for n, w in enumerate(words)], pack)))


def edge_runs(pages: Iterable[Sequence[Word]]) -> Counter[tuple[str, tuple[str, ...]]]:
    """How many pages of different content start (`"start"`) or end (`"end"`) with each run of three to twelve
    typed words that holds no digit. Pages with exactly the same words count once."""
    seen: Counter[tuple[str, tuple[str, ...]]] = Counter()
    distinct = {tuple(w.text for w in words): words for words in pages}
    for words in distinct.values():
        texts = [w.text for w in words]
        runs = set()
        for size in range(EDGE_WORDS, min(EDGE_MAX, len(texts)) + 1):
            head, tail = words[:size], words[-size:]
            if not any(w.handwritten or _DIGIT.search(w.text) for w in head):
                runs.add(("start", tuple(texts[:size])))
            if not any(w.handwritten or _DIGIT.search(w.text) for w in tail):
                runs.add(("end", tuple(texts[-size:])))
        seen.update(runs)
    return seen


def _edge(
    words: Sequence[Word],
    at: tuple[int, int],
    other: Sequence[Word],
    other_at: tuple[int, int],
    common: Mapping[Any, int],
) -> bool:
    """Whether the run `words[at]` is a header or footer the other page lacks (on `EDGE_PAGES` other pages)."""
    run = tuple(w.text for w in words[at[0] : at[1]])
    if not EDGE_WORDS <= len(run) <= EDGE_MAX:
        return False
    if at[0] == 0 and other_at[0] == 0:
        return common.get(("start", run), 0) - 1 >= EDGE_PAGES
    if at[1] == len(words) and other_at[0] == len(other):
        return common.get(("end", run), 0) - 1 >= EDGE_PAGES
    return False


def same_content(
    a: Sequence[Word], b: Sequence[Word], pack: MergedPack, common: Mapping[Any, int] | None = None
) -> bool:
    """Whether two pages show the same content (see the module notes). `common` is `edge_runs` of the bundle's pages."""
    common = common or {}
    typed_a, typed_b = _typed(a), _typed(b)
    if len(typed_a) < MIN_WORDS or len(typed_b) < MIN_WORDS:
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
            if all(w.handwritten for w in extra):
                added += len(extra)
            elif any(w.handwritten for w in extra):
                return False
            elif not (
                _edge(a, (i1, i2), b, (j1, j2), common) if op == "delete" else _edge(b, (j1, j2), a, (i1, i2), common)
            ):
                return False
            continue
        if i2 - i1 != j2 - j1:
            if _number(" ".join(words_a[i1:i2])) != _number(" ".join(words_b[j1:j2])):
                return False
            if not all(_DIGIT.search(w) for w in (*words_a[i1:i2], *words_b[j1:j2])):
                return False
            continue
        for x, y in zip(words_a[i1:i2], words_b[j1:j2], strict=True):
            if _DIGIT.search(x) or _DIGIT.search(y):
                if _number(x) != _number(y):
                    return False
            elif fuzz.ratio(x, y) < MISREAD:
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
