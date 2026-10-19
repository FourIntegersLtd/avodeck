"""Matching the expert's pages to the court bundle (`match_trial_bundle`, screen 23).

Pages are matched by what is on them, never by their numbers:

1. candidates from MinHash over character 4-grams of the normalised text (`datasketch` LSH);
2. a closer comparison of each candidate (`rapidfuzz` ratio), falling back to the whole bundle when no
   candidate is close enough;
3. one trial page per page, best scores first;
4. runs: an unmatched page whose neighbours matched next to each other may take the trial page between
   them, as a weaker match the expert confirms.

A score of `AUTO_CONFIRM` or more is confirmed; `MIN_SCORE` to `AUTO_CONFIRM` waits for the expert; below
`MIN_SCORE` the page is missing. Text only: no page text reaches a log (rule 6).
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

from datasketch import MinHash, MinHashLSH
from rapidfuzz import fuzz, process

AUTO_CONFIRM = 0.98
MIN_SCORE = 0.6
RUN_FLOOR = 0.45  # a page in a run of matched neighbours may match from here, never confirmed
NUM_PERM = 64
LSH_THRESHOLD = 0.5
SHINGLE = 4

_NOT_WORD = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class OurPage:
    key: Hashable
    page_no: int
    text: str


@dataclass(frozen=True)
class Match:
    key: Hashable
    trial_page_no: int | None
    score: float
    method: str  # text, sequence or none
    confirmed: bool


def normalise(text: str) -> str:
    return " ".join(_NOT_WORD.sub(" ", text.lower()).split())


def _shingles(text: str) -> set[bytes]:
    if len(text) <= SHINGLE:
        return {text.encode()} if text else set()
    return {text[i : i + SHINGLE].encode() for i in range(len(text) - SHINGLE + 1)}


def _score(a: str, b: str) -> float:
    return float(fuzz.ratio(a, b)) / 100 if a and b else 0.0


def match_pages(ours: Sequence[OurPage], theirs: Sequence[str]) -> list[Match]:
    """Each of `ours` with its trial page number (1-based position in `theirs`) or none."""
    mine = [normalise(p.text) for p in ours]
    trial = [normalise(t) for t in theirs]
    lsh = MinHashLSH(threshold=LSH_THRESHOLD, num_perm=NUM_PERM)
    for i, sig in enumerate(MinHash.bulk([_shingles(t) for t in trial], num_perm=NUM_PERM)):
        if trial[i]:
            lsh.insert(i, sig)
    pairs: list[tuple[float, int, int]] = []
    for n, sig in enumerate(MinHash.bulk([_shingles(t) for t in mine], num_perm=NUM_PERM)):
        if not mine[n]:
            continue
        scored = [(_score(mine[n], trial[i]), n, i) for i in lsh.query(sig)]
        if not any(s >= MIN_SCORE for s, _, _ in scored):
            found = process.extract(mine[n], trial, scorer=fuzz.ratio, score_cutoff=MIN_SCORE * 100, limit=5)
            scored += [(float(s) / 100, n, int(i)) for _, s, i in found]
        pairs += [p for p in scored if p[0] >= MIN_SCORE]
    taken: dict[int, tuple[int, float, str]] = {}
    used: set[int] = set()
    for score, n, i in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if n not in taken and i not in used:
            taken[n] = (i, score, "text")
            used.add(i)
    _runs(ours, mine, trial, taken, used)
    out = []
    for n, page in enumerate(ours):
        if n in taken:
            i, score, method = taken[n]
            confirmed = method == "text" and score >= AUTO_CONFIRM
            out.append(Match(page.key, i + 1, round(score, 4), method, confirmed))
        else:
            out.append(Match(page.key, None, 0.0, "none", False))
    return out


def _runs(
    ours: Sequence[OurPage],
    mine: list[str],
    trial: list[str],
    taken: dict[int, tuple[int, float, str]],
    used: set[int],
) -> None:
    """An unmatched page between two neighbours matched one trial page apart takes the page between."""
    by_no = {p.page_no: n for n, p in enumerate(ours)}
    for n, page in enumerate(ours):
        if n in taken:
            continue
        before, after = by_no.get(page.page_no - 1), by_no.get(page.page_no + 1)
        if before not in taken or after not in taken:
            continue
        i = taken[before][0] + 1
        if taken[after][0] != i + 1 or i in used:
            continue
        score = _score(mine[n], trial[i])
        if score >= RUN_FLOOR:
            taken[n] = (i, max(score, MIN_SCORE), "sequence")
            used.add(i)
