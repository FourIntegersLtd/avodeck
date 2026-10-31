"""Slice B: reading by transcript (ADR 0028). The builder and the server's own decision that a page is read.

- `transcript.entries(...)` lays the case's record entries out in order (event date, undated last, then page and
  position), one list of printed lines each, with the expert's word corrections applied and names and dates of
  birth stripped (rule 7). Copies, replaced pages, set-aside pages and unreadable pages give no entries.
  Entries of an administrative kind (pack `transcript: exclude`) fold to one short line.
  It carries no fact and no suggestion (rule 3): only the words on the page.
- `transcript.record_seen(...)` stores what the reader reported seeing (`transcript_views`, append-only). The
  server decides: a page is read when every entry on it has been seen for `SEEN_MS` in all, or, for a page with
  no entries, when its picture was opened. The read goes through the same hash-chained log as a page read, with
  `via = 'transcript'`, once per person and page.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import psycopg
import pytest

from app.core.contracts.reading import page_read_log
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from app.features.reader import repo, transcript
from tests.support.contracts import add_fact
from tests.support.db import scope_for
from tests.support.identity import NAME, SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH
from tests.support.seed import Seed
from tests.support.transcript_world import Spec, add_document, add_page

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
LIBRARY = default_library()


@pytest.fixture
async def aconn(admin_dsn: str) -> Any:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, stranger = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    return {"case": case, "expert": expert, "assistant": assistant, "stranger": stranger}


async def listed(api_db: Database, w: dict[str, Any], person: uuid.UUID | None = None) -> transcript.Listing:
    async with api_db.request(scope_for(person or w["expert"])) as conn:
        return await transcript.entries(conn, CRYPTO, LIBRARY, w["case"], offset=0, limit=100)


async def test_entries_come_in_date_order_with_the_undated_last(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("later note",), day=date(2020, 5, 1))])
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_session", ("undated note",))])
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [Spec("zz_session", ("earlier note",), day=date(2019, 1, 1))])
    got = await listed(api_db, world)
    assert [e.page_no for e in got.entries] == [3, 1, 2]
    assert got.total == 3
    assert got.entries[0].event_date == date(2019, 1, 1)


async def test_an_entry_is_its_printed_lines_with_the_experts_corrections_in_place(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(
        aconn,
        CRYPTO,
        world["case"],
        doc,
        1,
        [Spec("zz_session", ("first line here", "second line"), role="zz_practitioner")],
    )
    got = (await listed(api_db, world)).entries[0]
    assert got.lines == ["first line here", "second line"]
    assert got.author_role == "zz_practitioner"
    assert got.id == built.entry_ids[0]
    assert got.folded is False


async def test_copies_replaced_and_set_aside_pages_give_no_entries(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    keep = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("kept",))])
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_session", ("copy",))], duplicate_of=keep.page_id)
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [Spec("zz_session", ("old",))], superseded_by=keep.page_id)
    await add_page(aconn, CRYPTO, world["case"], doc, 4, [Spec("zz_session", ("aside",))], quarantined=True)
    assert [e.page_no for e in (await listed(api_db, world)).entries] == [1]


async def test_an_administrative_entry_folds_to_one_short_line(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    long_text = "one two three four five six seven eight nine ten eleven twelve"
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_booking", (long_text, "next line"))])
    got = (await listed(api_db, world)).entries[0]
    assert got.folded is True
    assert len(got.lines) == 1
    assert got.lines[0].startswith("one two three")
    assert "twelve" not in got.lines[0]


async def test_names_and_dates_of_birth_are_stripped_from_every_entry(
    api_db: Database, aconn: psycopg.AsyncConnection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    world = {"case": case, "expert": expert}
    await record_identity(aconn, CRYPTO, world["case"])
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", (SENTENCE,))])
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_booking", (f"{NAME} booked in",))])
    text = [line for e in (await listed(api_db, world)).entries for line in e.lines]
    assert leaked(text) == []
    assert NAME.split()[1].casefold() not in " ".join(text).casefold()


async def read_by_expert(api_db: Database, w: dict[str, Any], page_id: uuid.UUID) -> None:
    async with api_db.request(scope_for(w["expert"])) as conn:
        await repo.record_read(conn, w["case"], page_id, w["expert"], 30)


async def test_a_result_entry_names_the_result_groups_of_its_verified_facts(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    one = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_reading", ("ZZLEVEL was high",))])
    two = await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_reading", ("ZZLEVEL was low",))])
    three = await add_page(aconn, CRYPTO, world["case"], doc, 3, [Spec("zz_reading", ("nothing named",))])
    add_fact(admin, world["case"], one.page_id, status="verified", value="ZZLEVEL", word_ids=one.word_ids[:1])
    add_fact(admin, world["case"], two.page_id, status="suggested", value="ZZLEVEL", word_ids=two.word_ids[:1])
    add_fact(admin, world["case"], three.page_id, status="verified", value="x", word_ids=three.word_ids[:1])
    for built in (one, two, three):
        await read_by_expert(api_db, world, built.page_id)
    first, second, third = (await listed(api_db, world)).entries
    assert first.results == ["zz_level"] and first.result_labels == ["zz.result.level"]
    assert second.results == []  # not checked yet: the raw words are never matched
    assert third.results == []


async def test_a_verified_result_on_a_page_the_named_expert_has_not_read_is_not_named(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    one = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_reading", ("ZZLEVEL was high",))])
    add_fact(admin, world["case"], one.page_id, status="verified", value="ZZLEVEL", word_ids=one.word_ids[:1])
    assert (await listed(api_db, world)).entries[0].results == []


async def test_an_entry_names_its_kind_and_author_by_the_packs_label_keys(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("a",), role="zz_practitioner")])
    got = (await listed(api_db, world)).entries[0]
    assert got.kind_label == "zz.entry.session"
    assert got.author_label == "zz.author.practitioner"


async def test_an_unreadable_page_that_has_entries_is_listed_but_one_with_none_is_a_picture_page(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("blur",))], unreadable=True)
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [])
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [Spec("zz_session", ("fine",))])
    got = await listed(api_db, world)
    assert [e.page_no for e in got.entries] == [1, 3]
    assert got.picture_pages == [2]


async def test_someone_without_access_sees_no_entries(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("hidden",))])
    assert (await listed(api_db, world, world["stranger"])).entries == []


async def test_paging_returns_a_slice_and_the_total(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    for n in (1, 2, 3):
        await add_page(aconn, CRYPTO, world["case"], doc, n, [Spec("zz_session", (f"note {n}",), day=date(2020, 1, n))])
    async with api_db.request(scope_for(world["expert"])) as conn:
        got = await transcript.entries(conn, CRYPTO, LIBRARY, world["case"], offset=1, limit=1)
    assert [e.page_no for e in got.entries] == [2]
    assert got.total == 3


# --- the server decides when a page is read ---------------------------------------------------------


async def seen(api_db: Database, w: dict[str, Any], person: uuid.UUID, **kw: Any) -> list[int]:
    async with api_db.request(scope_for(person)) as conn:
        return await transcript.record_seen(conn, w["case"], person, **kw)


async def test_a_page_is_read_when_every_entry_on_it_has_been_seen_long_enough(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(
        aconn, CRYPTO, world["case"], doc, 7, [Spec("zz_session", ("a",)), Spec("zz_session", ("b",))]
    )
    first, second = built.entry_ids
    expert = world["expert"]
    assert await seen(api_db, world, expert, entry_id=first, dwell_ms=transcript.SEEN_MS) == []
    assert await seen(api_db, world, expert, entry_id=second, dwell_ms=transcript.SEEN_MS - 1) == []
    assert await seen(api_db, world, expert, entry_id=second, dwell_ms=1) == [7]
    async with api_db.request(scope_for(expert)) as conn:
        log = await page_read_log(conn, world["case"])
    assert [(e.page_no, e.person_id, e.counts_for_citing, e.via) for e in log] == [(7, expert, True, "transcript")]


async def test_a_page_already_read_is_not_read_twice(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("a",))])
    expert = world["expert"]
    entry = built.entry_ids[0]
    assert await seen(api_db, world, expert, entry_id=entry, dwell_ms=transcript.SEEN_MS) == [1]
    assert await seen(api_db, world, expert, entry_id=entry, dwell_ms=transcript.SEEN_MS) == []
    async with api_db.request(scope_for(expert)) as conn:
        assert len(await page_read_log(conn, world["case"])) == 1


async def test_a_page_with_no_entries_is_read_when_its_picture_is_opened(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [])
    assert await seen(api_db, world, world["expert"], page_no=3, picture_opened=True) == [3]


async def test_opening_the_picture_of_a_page_with_entries_does_not_read_it(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [Spec("zz_session", ("a",))])
    assert await seen(api_db, world, world["expert"], page_no=3, picture_opened=True) == []


async def test_a_picture_is_not_enough_while_the_entries_are_not_ready(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 3, [], analysed=False)
    with pytest.raises(transcript.NotReady):
        await seen(api_db, world, world["expert"], page_no=3, picture_opened=True)
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await page_read_log(conn, world["case"]) == []


async def test_an_unreadable_page_with_entries_needs_its_entries_seen_and_its_picture_opened(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 4, [Spec("zz_session", ("blur",))], unreadable=True)
    expert, entry = world["expert"], built.entry_ids[0]
    assert await seen(api_db, world, expert, entry_id=entry, dwell_ms=transcript.SEEN_MS) == []
    assert await seen(api_db, world, expert, page_no=4, picture_opened=True) == [4]


async def test_an_unreadable_page_with_entries_is_not_read_by_its_picture_alone(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 4, [Spec("zz_session", ("blur",))], unreadable=True)
    assert await seen(api_db, world, world["expert"], page_no=4, picture_opened=True) == []


@pytest.mark.parametrize("kind", ["quarantined", "duplicate", "superseded"])
async def test_entries_on_a_page_that_is_not_to_be_read_are_refused(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any], kind: str
) -> None:
    doc = await add_document(aconn, world["case"])
    keep = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("kept",))])
    options: dict[str, dict[str, Any]] = {
        "quarantined": {"quarantined": True},
        "duplicate": {"duplicate_of": keep.page_id},
        "superseded": {"superseded_by": keep.page_id},
    }
    extra = options[kind]
    built = await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_session", ("x",))], **extra)
    with pytest.raises(transcript.UnknownEntry):
        await seen(api_db, world, world["expert"], entry_id=built.entry_ids[0], dwell_ms=transcript.SEEN_MS)
    with pytest.raises(transcript.UnknownEntry):
        await seen(api_db, world, world["expert"], page_no=2, picture_opened=True)
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await page_read_log(conn, world["case"]) == []


async def test_each_person_reads_for_themselves(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("a",))])
    entry = built.entry_ids[0]
    await seen(api_db, world, world["expert"], entry_id=entry, dwell_ms=transcript.SEEN_MS)
    assert await seen(api_db, world, world["assistant"], entry_id=entry, dwell_ms=transcript.SEEN_MS) == [1]
    async with api_db.request(scope_for(world["assistant"])) as conn:
        log = [(e.person_id, e.counts_for_citing) for e in await page_read_log(conn, world["case"])]
    assert (world["assistant"], False) in log


async def test_an_entry_of_another_case_is_refused(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any], seed: Seed
) -> None:
    other = seed.case(seed.org(kind="personal", members={world["expert"]: "owner"}), world["expert"], status="active")
    doc = await add_document(aconn, other)
    built = await add_page(aconn, CRYPTO, other, doc, 1, [Spec("zz_session", ("a",))])
    with pytest.raises(transcript.UnknownEntry):
        await seen(api_db, world, world["expert"], entry_id=built.entry_ids[0], dwell_ms=transcript.SEEN_MS)


async def test_views_cannot_be_changed_or_removed(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("a",))])
    await seen(api_db, world, world["expert"], entry_id=built.entry_ids[0], dwell_ms=10)
    with pytest.raises(psycopg.Error):
        admin.execute("update app.transcript_views set dwell_ms = 0")
    with pytest.raises(psycopg.Error):
        admin.execute("delete from app.transcript_views")
