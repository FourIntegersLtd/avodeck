"""The records transcript (ADR 0028): the case's record entries laid out for reading, and the server's own
decision that a page has been read by reading its entries.

An entry is the words of one run on a page (`record_entries`, slice A), set out in the printed lines, with the
expert's corrections in place and every name and date of birth of the case stripped (rule 7). It carries
no fact and no suggestion (rule 3). Nothing here is logged (rule 6).

`record_seen` stores what the reader reported (`transcript_views`, append-only). The server decides: a
page is read when each of its entries has been seen for `SEEN_MS` in all, or, for a page with no
entries, when its picture was opened. That read goes through the same hash-chained log as a page read.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from psycopg import AsyncConnection

from app.core.ai import pii
from app.core.contracts.case_identity import identity_for
from app.core.contracts.document_words import apply_corrections, current_corrections, word_purpose
from app.core.contracts.record_entries import RecordEntry, entries_for_case
from app.core.crypto import CaseCrypto
from app.core.packs import PackLibrary
from app.features.reader import repo

# How long an entry must be on the reader's screen, in all, for it to count as seen.
SEEN_MS = 1500
# An administrative entry is folded to this many words.
FOLD_WORDS = 8


def line_numbers(boxes: Iterable[tuple[str, Sequence[float]]]) -> dict[str, int]:
    """Which printed line each word is on, from its box (`x1,y1,x2,y2,...`) in reading order: a word starts a
    new line when its middle is further from the word before than 0.6 of the taller word's height. A word with
    no readable box gets a line of its own."""
    out: dict[str, int] = {}
    line = 0
    previous: tuple[float, float] | None = None  # middle and height of the word before
    for word_id, box in boxes:
        ys = list(box[1::2])
        height = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
        if height <= 0:
            line += 2
            out[word_id] = line
            line += 2
            previous = None
            continue
        middle = (max(ys) + min(ys)) / 2
        if previous is not None and abs(middle - previous[0]) > 0.6 * max(height, previous[1]):
            line += 1
        out[word_id] = line
        previous = (middle, height)
    return out


class UnknownEntry(Exception):
    """The entry or page named is not one of this case."""


@dataclass(frozen=True)
class TranscriptEntry:
    id: uuid.UUID
    page_no: int
    kind: str
    folded: bool
    author_role: str | None
    specialty: str | None
    event_date: date | None
    date_basis: str | None
    lines: list[str] = field(repr=False)
    results: list[str]


@dataclass(frozen=True)
class Listing:
    entries: list[TranscriptEntry]
    total: int


async def _readable_pages(conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
    """Pages whose entries are laid out: not a copy, not replaced, not set aside, not unreadable."""
    cur = await conn.execute(
        "select id from app.pages where case_id = %s and not quarantined and not unreadable"
        " and duplicate_of_page_id is null and superseded_by_page_id is null",
        (case_id,),
    )
    return {row[0] for row in await cur.fetchall()}


async def _page_lines(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[int, tuple[int, str]]]:
    """Per page: word number -> (printed line, text), the expert's readings in place (kept in memory only)."""
    corrections = await current_corrections(conn, crypto, case_id, page_ids)
    out: dict[uuid.UUID, dict[int, tuple[int, str]]] = {}
    for page in page_ids:
        cur = await conn.execute(
            "select n, word_id, text, polygon from app.ocr_words where page_id = %s order by n", (page,)
        )
        rows = await cur.fetchall()
        words = {
            w: (await crypto.decrypt(conn, case_id, bytes(t), purpose=word_purpose(page, w))).decode()
            for _, w, t, _ in rows
        }
        words = apply_corrections(words, corrections.get(page, []))
        lines = line_numbers((w, [float(v) for v in poly or []]) for _, w, _, poly in rows)
        out[page] = {int(n): (lines[w], words[w]) for n, w, _, _ in rows if words[w]}
    return out


def _results(text: str, groups: list[dict[str, Any]]) -> list[str]:
    found = []
    for group in groups:
        for term in group.get("terms", []):
            if re.search(rf"(?<!\w){re.escape(str(term))}(?!\w)", text, re.IGNORECASE):
                found.append(str(group["key"]))
                break
    return found


async def entries(
    conn: AsyncConnection, crypto: CaseCrypto, library: PackLibrary, case_id: uuid.UUID, *, offset: int, limit: int
) -> Listing:
    codes = await repo.case_codes(conn, case_id)
    if codes is None:
        return Listing([], 0)
    pack = library.for_case(codes)
    readable = await _readable_pages(conn, case_id)
    wanted: list[RecordEntry] = [e for e in await entries_for_case(conn, case_id) if e.page_id in readable]
    chosen = wanted[offset : offset + limit]
    if not chosen:
        return Listing([], len(wanted))
    included = pack.transcript_entry_kinds()
    known = {k["key"] for k in pack.entry_kinds()}
    groups = pack.result_groups()
    identity = await identity_for(conn, crypto, case_id)
    placed = await _page_lines(conn, crypto, case_id, list(dict.fromkeys(e.page_id for e in chosen)))
    out: list[TranscriptEntry] = []
    for e in chosen:
        words = placed[e.page_id]
        by_line: dict[int, list[str]] = {}
        for n in range(e.first_n, e.last_n + 1):
            if n in words:
                by_line.setdefault(words[n][0], []).append(words[n][1])
        text = "\n".join(" ".join(by_line[k]) for k in sorted(by_line))
        text = pii.strip(text, pack=pack, names=identity.names, dates_of_birth=identity.dates_of_birth).text
        lines = text.split("\n") if text else []
        folded = e.kind in known and e.kind not in included
        if folded:
            lines = [" ".join(" ".join(lines).split()[:FOLD_WORDS])]
        out.append(
            TranscriptEntry(
                e.id, e.page_no, e.kind, folded, e.author_role, e.specialty, e.event_date, e.date_basis, lines,
                _results(text, groups),
            )
        )  # fmt: skip
    return Listing(out, len(wanted))


async def record_seen(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    person_id: uuid.UUID,
    *,
    entry_id: uuid.UUID | None = None,
    page_no: int | None = None,
    dwell_ms: int = 0,
    picture_opened: bool = False,
) -> list[int]:
    """Stores one view and returns the page numbers the server now counts as read (at most one)."""
    if entry_id is not None:
        cur = await conn.execute(
            "select e.page_id, p.bundle_page_no from app.record_entries e join app.pages p on p.id = e.page_id"
            " where e.id = %s and e.case_id = %s",
            (entry_id, case_id),
        )
        found = await cur.fetchone()
        if found is None:
            raise UnknownEntry
        page_id, number = found
        await conn.execute(
            "insert into app.transcript_views (case_id, page_id, person_id, entry_id, dwell_ms)"
            " values (%s, %s, %s, %s, %s)",
            (case_id, page_id, person_id, entry_id, min(dwell_ms, 600_000)),
        )
    else:
        assert page_no is not None
        page_id = await repo.page_id_by_no(conn, case_id, page_no)
        if page_id is None:
            raise UnknownEntry
        number = page_no
        await conn.execute(
            "insert into app.transcript_views (case_id, page_id, person_id, picture_opened) values (%s, %s, %s, true)",
            (case_id, page_id, person_id),
        )
    if await repo.has_read(conn, case_id, number, person_id):
        return []
    cur = await conn.execute(
        "select count(*) filter (where coalesce(v.total, 0) < %(seen)s), count(*)"
        " from app.record_entries e left join (select entry_id, sum(dwell_ms) as total from app.transcript_views"
        "   where page_id = %(page)s and person_id = %(person)s and entry_id is not null group by entry_id) v"
        "   on v.entry_id = e.id where e.page_id = %(page)s",
        {"seen": SEEN_MS, "page": page_id, "person": person_id},
    )
    row = await cur.fetchone()
    assert row is not None
    short, count = row
    if count == 0:
        cur = await conn.execute(
            "select exists (select 1 from app.transcript_views where page_id = %s and person_id = %s"
            " and picture_opened)",
            (page_id, person_id),
        )
        done = bool((await cur.fetchone() or (False,))[0])
    else:
        done = short == 0
    if not done:
        return []
    cur = await conn.execute(
        "select coalesce(sum(dwell_ms), 0) from app.transcript_views where page_id = %s and person_id = %s",
        (page_id, person_id),
    )
    total = int((await cur.fetchone() or (0,))[0])
    await repo.record_read(conn, case_id, page_id, person_id, round(total / 1000), via="transcript")
    return [int(number)]
