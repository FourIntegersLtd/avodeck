"""The records transcript (ADR 0028): the case's record entries laid out for reading, and the server's own
decision that a page has been read by reading its entries.

An entry is the words of one run on a page (`record_entries`, slice A), set out in the printed lines, with the
expert's corrections in place. The record prints exactly as written, names and dates of birth included (founder
decision of 2026-11-04, ADR 0035): this is the named expert's own copy and it goes to no AI, and rule 7 covers
only text sent to an AI. Words that lie in no entry (a header, a footer, words between entries) travel with the
nearest entry as `before` and `after`, so every word the computer read is printed once. It carries no fact and
no suggestion (rule 3). Nothing here is logged (rule 6).

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

from app.core.contracts.document_words import apply_corrections, current_corrections, word_purpose
from app.core.contracts.facts import citable_facts
from app.core.contracts.reading import PageReads
from app.core.contracts.record_entries import RecordEntry, entries_for_case, entries_ready, entries_unread_pages
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack, PackLibrary
from app.features.reader import repo

# How long an entry must be on the reader's screen, in all, for it to count as seen.
SEEN_MS = 1500
# An administrative entry is folded to this many words.
FOLD_WORDS = 8


def rows(boxes: Iterable[tuple[str, Sequence[float]]]) -> list[list[str]]:
    """The printed rows of a run of words, from their boxes (`x1,y1,x2,y2,...`) given in reading order. Words whose
    vertical middles differ by at most 0.5 of the taller word's height stand on one row, whatever order they were
    read in, and run left to right; rows run top to bottom. A label column read first and its value column read
    next therefore print side by side. A word with no readable box keeps its place on a row of its own. Apply this
    to one entry (or one block of loose words) at a time, never across entries."""
    out: list[list[str]] = []
    block: list[tuple[str, float, float, float]] = []  # word, left edge, vertical middle, height

    def close() -> None:
        found: list[list[Any]] = []  # [middle, height, [(left, order, word)]] per row
        for order, (text, left, middle, height) in enumerate(block):
            for row in found:
                if abs(middle - row[0]) <= 0.5 * max(height, row[1]):
                    row[1] = max(row[1], height)
                    row[2].append((left, order, text))
                    break
            else:
                found.append([middle, height, [(left, order, text)]])
        found.sort(key=lambda row: row[0])  # stable: rows that tie keep the order they were met in
        out.extend([text for _, _, text in sorted(row[2])] for row in found)
        block.clear()

    for text, box in boxes:
        ys, xs = list(box[1::2]), list(box[0::2])
        height = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
        if height <= 0:
            close()
            out.append([text])
            continue
        block.append((text, min(xs), (max(ys) + min(ys)) / 2, height))
    close()
    return out


class UnknownEntry(Exception):
    """The entry or page named is not one of this case, or is not one to read (a copy, replaced or set aside)."""


class NotReady(Exception):
    """The case's entries are not all read yet, so no page can be said to have none."""


@dataclass(frozen=True)
class TranscriptEntry:
    id: uuid.UUID
    page_no: int
    kind: str
    kind_label: str
    folded: bool
    author_role: str | None
    author_label: str | None
    specialty: str | None
    event_date: date | None
    date_basis: str | None
    lines: list[str] = field(repr=False)
    results: list[str]
    result_labels: list[str]
    source_type: str | None = None  # the page's source type (pack key), for the file's sections
    specialty_label: str | None = None  # the pack's i18n key for the specialty
    section: str | None = None  # key of the pack section the entry is in (set by `entries`)
    before: list[str] = field(default_factory=list, repr=False)  # words on the page before it that lie in no entry
    after: list[str] = field(default_factory=list, repr=False)  # words after the last entry of a page, in no entry


@dataclass(frozen=True)
class Listing:
    entries: list[TranscriptEntry]
    total: int
    picture_pages: list[int] = field(default_factory=list)  # pages with no entries: read by opening the picture
    unread_pages: list[int] = field(default_factory=list)  # the computer could not read these: look at the picture
    # With `full`, on the call that reaches the last entry: the lines of every page with no entries (the files print
    # them under the page number); empty otherwise.
    picture_words: dict[int, list[str]] = field(default_factory=dict, repr=False)
    # Words (as the files count them) the computer read on each page this call lists: the number the files must
    # print, once each. The caller merges the calls; a page is the same on every call that lists it.
    words_by_page: dict[int, int] = field(default_factory=dict)


Pages = dict[uuid.UUID, tuple[int, str | None]]


async def _pages_to_read(conn: AsyncConnection, case_id: uuid.UUID) -> Pages:
    """Page id -> (number, source type) of the pages to read: not a copy, not replaced, not set aside."""
    cur = await conn.execute(
        "select id, bundle_page_no, source_type from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s) and not quarantined"
        " and duplicate_of_page_id is null and superseded_by_page_id is null",
        (case_id,),
    )
    return {row[0]: (int(row[1]), row[2]) for row in await cur.fetchall()}


Word = tuple[int, str, list[float]]  # number on the page, text (the expert's reading in place), box


async def _page_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Word]]:
    """Per page, its words in reading order, the expert's readings in place (kept in memory only)."""
    corrections = await current_corrections(conn, crypto, case_id, page_ids)
    out: dict[uuid.UUID, list[Word]] = {}
    for page in page_ids:
        cur = await conn.execute(
            "select n, word_id, text, polygon from app.ocr_words where page_id = %s order by n", (page,)
        )
        found = await cur.fetchall()
        texts = {
            w: (await crypto.decrypt(conn, case_id, bytes(t), purpose=word_purpose(page, w))).decode()
            for _, w, t, _ in found
        }
        texts = apply_corrections(texts, corrections.get(page, []))
        out[page] = [(int(n), texts[w], [float(v) for v in poly or []]) for n, w, _, poly in found if texts[w]]
    return out


def _lines(words: Iterable[Word]) -> list[str]:
    """The printed lines of these words (one entry, or one block of loose words)."""
    return [" ".join(row) for row in rows((text, box) for _, text, box in words)]


def _around(page_entries: Sequence[RecordEntry], words: list[Word]) -> dict[uuid.UUID, tuple[list[Word], list[Word]]]:
    """Entry id -> (words before it, words after it) for the words of the page that lie in no entry of the page:
    a loose word goes before the next entry on the page, else after the last one."""
    ordered = sorted(page_entries, key=lambda e: (e.first_n, e.last_n))
    out: dict[uuid.UUID, tuple[list[Word], list[Word]]] = {e.id: ([], []) for e in ordered}
    for word in words:
        n = word[0]
        if any(e.first_n <= n <= e.last_n for e in ordered):
            continue
        following = next((e for e in ordered if e.first_n > n), None)
        if following is not None:
            out[following.id][0].append(word)
        else:
            out[ordered[-1].id][1].append(word)
    return out


def _tokens(words: Iterable[Word]) -> int:
    """How many words the files count in these (a word is whatever the printed line splits on)."""
    return sum(len(text.split()) for _, text, _ in words)


def _results(values: Iterable[str], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pack's result groups whose words appear in the values of checked facts."""
    found = []
    for group in groups:
        for term in group.get("terms", []):
            if any(re.search(rf"(?<!\w){re.escape(str(term))}(?!\w)", v, re.IGNORECASE) for v in values):
                found.append(group)
                break
    return found


async def _checked_values(
    conn: AsyncConnection, case_id: uuid.UUID, chosen: list[RecordEntry]
) -> dict[uuid.UUID, list[str]]:
    """Entry id -> values of the checked (verified) facts the named expert may cite whose words lie in the entry."""
    facts = await citable_facts(conn, case_id, PageReads())
    if not facts:
        return {}
    cur = await conn.execute(
        "select page_id, word_id, n from app.ocr_words where page_id = any(%s)", ([f.page_id for f in facts],)
    )
    number = {(row[0], row[1]): int(row[2]) for row in await cur.fetchall()}
    out: dict[uuid.UUID, list[str]] = {}
    for e in chosen:
        for f in facts:
            if f.page_id != e.page_id or not f.value:
                continue
            if any(e.first_n <= number.get((f.page_id, w), -1) <= e.last_n for w in f.word_ids):
                out.setdefault(e.id, []).append(f.value)
    return out


@dataclass
class _Survey:
    pack: MergedPack
    wanted: list[RecordEntry]  # in section order, then date order
    sections: list[tuple[str, str]]  # key, label of the section of each wanted entry
    sources: dict[uuid.UUID, str | None]  # page id -> source type
    set_aside: int
    listing_pages: Pages
    picture: list[int]
    unread: list[int]
    on_page: dict[uuid.UUID, list[RecordEntry]] = field(default_factory=dict)  # every entry of each page to read


async def _survey(
    conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID, only_read_by: uuid.UUID | None
) -> _Survey | None:
    """Which entries there are and where each belongs; no text is opened (rule 6)."""
    from app.features.reader.transcript_layout import place

    codes = await repo.case_codes(conn, case_id)
    if codes is None:
        return None
    pack = library.for_case(codes)
    pages = await _pages_to_read(conn, case_id)
    cur = await conn.execute(
        "select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)", (case_id,)
    )
    set_aside = int((await cur.fetchone() or (0,))[0]) - len(pages)
    if only_read_by is not None:
        allowed = await PageReads().read_page_ids(conn, case_id, only_read_by)
        pages = {p: v for p, v in pages.items() if p in allowed}
    everything = await entries_for_case(conn, case_id)
    included = pack.transcript_entry_kinds()
    known = {k["key"] for k in pack.entry_kinds()}
    placed = [
        (
            place(
                pack, source_type=pages[e.page_id][1], kind=e.kind, folded=e.kind in known and e.kind not in included
            ),
            e,
        )
        for e in everything
        if e.page_id in pages
    ]
    placed.sort(key=lambda pe: pe[0][0])  # stable: the date order stays inside a section
    with_entries = {e.page_id for e in everything}
    on_page: dict[uuid.UUID, list[RecordEntry]] = {}
    for e in everything:
        if e.page_id in pages:
            on_page.setdefault(e.page_id, []).append(e)
    failed = set(await entries_unread_pages(conn, case_id))
    unread = sorted(n for p, (n, _) in pages.items() if n in failed and p not in with_entries)
    picture = (
        sorted(n for p, (n, _) in pages.items() if p not in with_entries and n not in failed)
        if only_read_by is None
        else []
    )
    return _Survey(
        pack, [e for _, e in placed], [(k, lb) for (_, k, lb), _ in placed], {p: v[1] for p, v in pages.items()},
        set_aside, pages, picture, unread, on_page,
    )  # fmt: skip


@dataclass(frozen=True)
class SectionDetail:
    first_date: date | None
    last_date: date | None
    pages: int  # distinct bundle pages the section's entries come from
    years: list[tuple[int | None, int, int]]  # (year or None, position of its first entry in the paged list, count)


@dataclass(frozen=True)
class Outline:
    sections: list[tuple[str, str, int, int]]  # key, label, count, offset
    total_pages: int
    with_entries: int
    no_entries: int
    unreadable: int
    set_aside: int
    details: dict[str, SectionDetail] = field(default_factory=dict)  # by section key


def _details(wanted: list[RecordEntry], sections: list[tuple[str, str]]) -> dict[str, SectionDetail]:
    """What each section holds. Entries are already in date order inside a section, undated last, so the years
    are runs of consecutive entries (not sorted again)."""
    found: dict[str, list[tuple[int, RecordEntry]]] = {}
    for position, (e, (key, _)) in enumerate(zip(wanted, sections, strict=True)):
        found.setdefault(key, []).append((position, e))
    out: dict[str, SectionDetail] = {}
    for key, members in found.items():
        dated = [e.event_date for _, e in members if e.event_date is not None]
        years: list[tuple[int | None, int, int]] = []
        for position, e in members:
            year = e.event_date.year if e.event_date is not None else None
            if years and years[-1][0] == year:
                years[-1] = (year, years[-1][1], years[-1][2] + 1)
            else:
                years.append((year, position, 1))
        out[key] = SectionDetail(
            min(dated, default=None), max(dated, default=None), len({e.page_id for _, e in members}), years
        )
    return out


async def outline(conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID) -> Outline:
    """The sections (with where each starts in the paged list) and the page coverage. Counts only."""
    got = await _survey(conn, library, case_id, None)
    if got is None:
        return Outline([], 0, 0, 0, 0, 0)
    sections: list[tuple[str, str, int, int]] = []
    for position, (key, label) in enumerate(got.sections):
        if sections and sections[-1][0] == key:
            sections[-1] = (key, label, sections[-1][2] + 1, sections[-1][3])
        else:
            sections.append((key, label, 1, position))
    return Outline(
        sections, len(got.listing_pages), len({e.page_id for e in got.wanted}), len(got.picture), len(got.unread),
        got.set_aside, _details(got.wanted, got.sections),
    )  # fmt: skip


async def entries(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    library: PackLibrary,
    case_id: uuid.UUID,
    *,
    offset: int,
    limit: int,
    only_read_by: uuid.UUID | None = None,
    full: bool = False,
) -> Listing:
    """The entries to read, in section order and then date order. With `only_read_by`, only pages that person's
    reading counts for. With `full`, an administrative entry keeps all its lines (the files print it in full); it
    stays `folded`, and the words of the pages with no entries come with the call that reaches the last entry.
    The words of a page that lie in no entry come with the entry after them (`before`), or the last entry of the
    page (`after`); every entry range of the page is considered, so paging and `only_read_by` change nothing."""
    got = await _survey(conn, library, case_id, only_read_by)
    if got is None:
        return Listing([], 0)
    pack, wanted = got.pack, got.wanted
    chosen = wanted[offset : offset + limit]
    section_keys = got.sections[offset : offset + limit]
    included = pack.transcript_entry_kinds()
    kind_labels = {k["key"]: str(k.get("label", k["key"])) for k in pack.entry_kinds()}
    role_labels = {r["key"]: str(r.get("label", r["key"])) for r in pack.author_roles()}
    specialty_labels = {s["key"]: str(s.get("label", s["key"])) for s in pack.entry_specialties()}
    groups = pack.result_groups()
    chosen_pages = list(dict.fromkeys(e.page_id for e in chosen))
    # The pages with no entries print their words in the files: on the call that reaches the last entry.
    last_call = offset + limit >= len(wanted)
    numbers = {p: n for p, (n, _) in got.listing_pages.items()}
    pictured = set(got.picture)
    bare = [p for p, n in numbers.items() if n in pictured] if full and last_call else []
    placed = await _page_words(conn, crypto, case_id, [*chosen_pages, *bare])
    checked = await _checked_values(conn, case_id, chosen)
    around = {id_: sides for page in chosen_pages for id_, sides in _around(got.on_page[page], placed[page]).items()}
    out: list[TranscriptEntry] = []
    for e, (section_key, _) in zip(chosen, section_keys, strict=True):
        before, after = around[e.id]
        lines = _lines(w for w in placed[e.page_id] if e.first_n <= w[0] <= e.last_n)
        folded = e.kind in kind_labels and e.kind not in included
        if folded and not full:
            lines = [" ".join(" ".join(lines).split()[:FOLD_WORDS])]
        named = _results(checked.get(e.id, []), groups)
        out.append(
            TranscriptEntry(
                e.id, e.page_no, e.kind, kind_labels.get(e.kind, e.kind), folded, e.author_role,
                role_labels.get(e.author_role) if e.author_role else None, e.specialty, e.event_date, e.date_basis,
                lines, [str(g["key"]) for g in named], [str(g.get("label", g["key"])) for g in named],
                got.sources.get(e.page_id), specialty_labels.get(e.specialty) if e.specialty else None, section_key,
                _lines(before), _lines(after),
            )
        )  # fmt: skip
    return Listing(
        out, len(wanted), got.picture, got.unread,
        {numbers[p]: _lines(placed[p]) for p in bare},
        {numbers[p]: _tokens(placed[p]) for p in [*chosen_pages, *bare]},
    )  # fmt: skip


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
    live = "not p.quarantined and p.duplicate_of_page_id is null and p.superseded_by_page_id is null"
    if entry_id is not None:
        cur = await conn.execute(
            "select e.page_id, p.bundle_page_no, p.unreadable from app.record_entries e"
            " join app.pages p on p.id = e.page_id where e.id = %s and e.bundle_id = (select bundle_id from app.cases "
            f"where id = %s) and {live}",
            (entry_id, case_id),
        )
        found = await cur.fetchone()
        if found is None:
            raise UnknownEntry
        page_id, number, unreadable = found
        await conn.execute(
            "insert into app.transcript_views (case_id, page_id, person_id, entry_id, dwell_ms)"
            " values (%s, %s, %s, %s, %s)",
            (case_id, page_id, person_id, entry_id, min(dwell_ms, 600_000)),
        )
    else:
        assert page_no is not None
        cur = await conn.execute(
            "select p.id, p.unreadable from app.pages p where p.bundle_id = (select bundle_id from app.cases where id "
            f"= %s) and p.bundle_page_no = %s and {live}",
            (case_id, page_no),
        )
        found = await cur.fetchone()
        if found is None:
            raise UnknownEntry
        if not await entries_ready(conn, case_id):
            raise NotReady
        page_id, unreadable = found
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
    cur = await conn.execute(
        "select exists (select 1 from app.transcript_views where page_id = %s and person_id = %s and picture_opened)",
        (page_id, person_id),
    )
    opened = bool((await cur.fetchone() or (False,))[0])
    # A page with no entries is read by opening its picture. An unreadable page that has entries needs both.
    if count == 0:
        done = opened and await entries_ready(conn, case_id)
    else:
        done = short == 0 and (opened or not unreadable)
    if not done:
        return []
    cur = await conn.execute(
        "select coalesce(sum(dwell_ms), 0) from app.transcript_views where page_id = %s and person_id = %s",
        (page_id, person_id),
    )
    total = int((await cur.fetchone() or (0,))[0])
    await repo.record_read(conn, case_id, page_id, person_id, round(total / 1000), via="transcript")
    return [int(number)]
