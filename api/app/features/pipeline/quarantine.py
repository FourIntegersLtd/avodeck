"""Which pages may belong to someone else (F25, S5, architecture 10.3).

The pack's recognisers find each page's identifiers; values are normalised (dates of birth read in
the pack's date order) and kept in memory only, never stored or logged. The case subject's value of a
kind is the one the case knows, or else the one on the most pages (of the pages carrying a value the
case knows, when it knows any); a kind with no single leader is not used. A page is suggested when
it carries a value that is not the subject's and none that is.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Hashable, Mapping, Sequence
from datetime import date

from app.core.ai.pii import DATE_OF_BIRTH
from app.core.packs import MergedPack
from app.features.pipeline import dates

REASON = "identifiers_differ"  # the code the pipeline's suggestions carry

Found = Mapping[str, set[str]]


def identifiers(text: str, pack: MergedPack) -> dict[str, set[str]]:
    """The identifiers on a page, by kind, normalised for comparison."""
    out: dict[str, set[str]] = {}
    for start, end, kind in pack.identifier_recognisers()(text):
        raw = text[start:end]
        if kind == DATE_OF_BIRTH:
            read = dates.read_date(raw, pack)
            value = read.isoformat() if read else None
        else:
            value = re.sub(r"[\W_]+", "", raw).upper() or None
        if value:
            out.setdefault(kind, set()).add(value)
    return out


def known(dates_of_birth: Sequence[date]) -> dict[str, set[str]]:
    """What the case itself knows about its subject."""
    return {DATE_OF_BIRTH: {d.isoformat() for d in dates_of_birth}} if dates_of_birth else {}


def suggest[K: Hashable](pages: Mapping[K, Found], known: Found | None = None) -> list[K]:
    """The pages to suggest, in the order given."""
    subject: dict[str, set[str]] = {k: set(v) for k, v in (known or {}).items() if v}
    anchored = [f for f in pages.values() if any(f.get(k, set()) & v for k, v in subject.items())]
    counts: dict[str, Counter[str]] = {}
    for found in anchored if subject else pages.values():
        for kind, values in found.items():
            counts.setdefault(kind, Counter()).update(values)
    for kind, counter in counts.items():
        if kind in subject:
            continue
        ranked = counter.most_common(2)
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            subject[kind] = {ranked[0][0]}
    flagged: list[K] = []
    for page, found in pages.items():
        used = {k: v for k, v in found.items() if k in subject}
        if not used:
            continue
        matches = any(v & subject[k] for k, v in used.items())
        differs = any(v - subject[k] for k, v in used.items())
        if differs and not matches:
            flagged.append(page)
    return flagged
