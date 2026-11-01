"""Slice A: record entries (ADR 0028): where T-07b says one entry starts and ends on a page.

- The table, its row-level security and its place in the purge order.
- The read contract for slice B (`core/contracts/record_entries.py`): typed rows, no text, ordered by
  (event date with undated last, page number, first word).
- Extraction stores the entries that pass provenance and counts the rest.
- `extract_entries` reruns T-07b entries only for a case that has none, once, and says when it is ready.
Messages name counts only (rule 6).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.contracts import record_entries
from app.core.contracts.case_tables import CASE_TABLES, PURGE_ORDER
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from app.features.pipeline import extract
from tests.repository.test_pipeline_extract import _open
from tests.repository.test_pipeline_support import t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def add_entry(
    admin: psycopg.Connection,
    case: uuid.UUID,
    page: uuid.UUID,
    first: int,
    *,
    kind: str = "consultation",
    event_date: dt.date | None = None,
    last: int | None = None,
) -> uuid.UUID:
    row = admin.execute(
        "insert into app.record_entries"
        " (page_id, bundle_id, kind, first_n, last_n, event_date, extraction_version)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s, 'v1') returning id",
        (page, case, kind, first, last or first, event_date),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def test_the_table_is_a_case_table_in_the_purge_order(admin: psycopg.Connection) -> None:
    assert "record_entries" in CASE_TABLES and "record_entries" in PURGE_ORDER
    assert PURGE_ORDER.index("record_entries") < PURGE_ORDER.index("pages")
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None and list(row[0]) == list(PURGE_ORDER)


def test_the_columns_hold_no_text(admin: psycopg.Connection) -> None:
    cols = {
        r[0]: r[1]
        for r in admin.execute(
            "select column_name, data_type from information_schema.columns"
            " where table_schema = 'app' and table_name = 'record_entries'"
        ).fetchall()
    }
    assert set(cols) == {
        "id",
        "page_id",
        "bundle_id",
        "kind",
        "first_n",
        "last_n",
        "author_role",
        "specialty",
        "event_date",
        "date_basis",
        "extraction_version",
        "created_at",
    }
    assert "text" not in {
        cols[c] for c in cols if c not in ("kind", "author_role", "specialty", "date_basis", "extraction_version")
    }


def test_an_entry_cannot_run_backwards_or_name_another_cases_page(admin: psycopg.Connection, seed: Seed) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    other = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    page = seed.page(case, seed.document(case), 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        add_entry(admin, case, page, 5, last=2)
    with pytest.raises(psycopg.errors.CheckViolation):
        add_entry(admin, other, page, 1)


def test_people_without_access_see_no_entries(api_dsn: str, admin: psycopg.Connection, seed: Seed) -> None:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    page = seed.page(case, seed.document(case), 1)
    add_entry(admin, case, page, 1)
    for person, seen in ((expert, 1), (stranger, 0)):
        with psycopg.connect(api_dsn) as conn:
            conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
            row = conn.execute(
                "select count(*) from app.record_entries where bundle_id = (select bundle_id from app.cases where id = "
                "%s)",
                (case,),
            ).fetchone()
            assert row == (seen,)
            with pytest.raises(psycopg.Error):
                conn.execute(
                    "delete from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
                    (case,),
                )


async def test_entries_come_back_in_date_order_with_undated_last_and_no_text(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case)
    p1, p2, p3 = (seed.page(case, doc, n) for n in (1, 2, 3))
    late = add_entry(admin, case, p1, 1, event_date=dt.date(2024, 5, 1))
    none_b = add_entry(admin, case, p1, 9)
    early_b = add_entry(admin, case, p3, 4, event_date=dt.date(2020, 1, 2))
    early_a = add_entry(admin, case, p2, 1, event_date=dt.date(2020, 1, 2))
    none_a = add_entry(admin, case, p2, 3)
    same_page = add_entry(admin, case, p3, 1, event_date=dt.date(2020, 1, 2))
    async with api_db.request(scope_for(expert)) as conn:
        rows = await record_entries.entries_for_case(conn, case)
        only = await record_entries.entries_for_case(conn, case, page_ids=[p2])
    assert [r.id for r in rows] == [early_a, same_page, early_b, late, none_b, none_a]
    assert [r.id for r in only] == [early_a, none_a]
    first = rows[0]
    assert isinstance(first, record_entries.RecordEntry)
    assert {f.name for f in dataclasses.fields(first)} == {
        "id",
        "page_id",
        "page_no",
        "kind",
        "first_n",
        "last_n",
        "author_role",
        "specialty",
        "event_date",
        "date_basis",
    }
    assert first.page_no == 2


async def test_ready_means_every_extracted_page_has_had_its_entries_read(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    page = seed.page(case, seed.document(case), 1)
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_status(conn, case) == "idle"
        assert await record_entries.entries_ready(conn, case) is False
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, stage, extracted_at)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'extract', now())",
        (page, case),
    )
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is False
    admin.execute("update app.page_analysis set entries_extracted_at = now() where page_id = %s", (page,))
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is True
        assert await record_entries.entries_status(conn, case) == "ready"


def test_deleting_the_case_leaves_no_entries(admin: psycopg.Connection, worker_dsn: str, seed: Seed) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row())
    page = seed.page(case, seed.document(case), 1)
    add_entry(admin, case, page, 1)
    admin.execute("update app.cases set status = 'served' where id = %s", (case,))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')"
        " on conflict (case_id) do nothing",
        (case, b"wrapped"),
    )
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
        conn.execute("select app.purge_case(%s)", (case,))
    assert admin.execute(
        "select count(*) from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
        (case,),
    ).fetchone() == (0,)


# Extraction ------------------------------------------------------------------------------------


def answer_with(pack: Any, kind: str, words: Sequence[Any], entries: list[dict[str, Any]]) -> Any:
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
            "entries": entries,
        }
    )


async def opened(
    tmp_path: Path,
    bedrock: Any,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    combo: Combo = LAUNCH,
) -> Any:
    return await _open(
        combo,
        FixtureStore(tmp_path / "nothing-recorded"),
        bedrock,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )


def two_entries(pack: Any, words: Sequence[Any]) -> list[dict[str, Any]]:
    kind = pack.entry_kinds()[0]["key"]
    role = pack.author_roles()[0]["key"]
    return [
        {
            "kind": kind,
            "first_word_id": words[0].id,
            "last_word_id": words[2].id,
            "author_role": role,
            "specialty": None,
        },
        {"kind": kind, "first_word_id": "nope", "last_word_id": words[2].id, "author_role": None, "specialty": None},
        {
            "kind": kind,
            "first_word_id": words[3].id,
            "last_word_id": words[4].id,
            "author_role": None,
            "specialty": None,
        },
    ]


async def test_extraction_stores_the_entries_that_pass_provenance_and_counts_the_rest(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*LAUNCH.codes)
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    words = embedded_words(split_pages(document)[0]).words
    bedrock = ScriptedBedrock([answer_with(pack, "", words, two_entries(pack, words))])
    async with await opened(tmp_path, bedrock, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        await h.upload(document)
        assert set((await h.drain()).values()) == {"done"}
        (page,) = h.pages()
        rows = admin.execute(
            "select kind, first_n, last_n, author_role, event_date, extraction_version from app.record_entries"
            " where page_id = %s order by first_n",
            (page["id"],),
        ).fetchall()
        assert [(r[1], r[2]) for r in rows] == [(1, 3), (4, 5)]
        assert rows[0][3] == pack.author_roles()[0]["key"] and rows[1][3] is None
        assert rows[0][4] == dt.date(2024, 3, 12), "dated from the words inside the span"
        assert rows[0][5] == extract.PROMPT_VERSION
        done = admin.execute(
            "select entries_extracted_at is not null, entries_rejected from app.page_analysis where page_id = %s",
            (page["id"],),
        ).fetchone()
        assert done == (True, 1)
        assert len(t07b_requests(h)) == 1


async def test_the_entries_job_reads_entries_only_for_a_case_with_none_and_only_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*LAUNCH.codes)
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    words = embedded_words(split_pages(document)[0]).words
    good = answer_with(pack, "", words, two_entries(pack, words))
    bedrock = ScriptedBedrock([good, good])
    async with await opened(tmp_path, bedrock, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        await h.upload(document)
        await h.drain()
        monkeypatch.setattr(record_entries, "registry", h.registry)  # the harness runs its own queue
        facts_before = admin.execute("select count(*) from app.facts where case_id = %s", (h.case_id,)).fetchone()
        # A case extracted before entries existed: no entries, nothing marked.
        admin.execute(
            "delete from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        )
        admin.execute(
            "update app.page_analysis set entries_extracted_at = null"
            " where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        )
        async with api_db.request(scope_for(h.expert)) as conn:
            assert await record_entries.entries_ready(conn, h.case_id) is False
            await record_entries.request_entries(conn, h.case_id)
            assert await record_entries.entries_status(conn, h.case_id) == "preparing"
        assert set((await h.drain()).values()) == {"done"}
        count = admin.execute(
            "select count(*) from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        ).fetchone()
        assert count == (2,)
        assert (
            admin.execute("select count(*) from app.facts where case_id = %s", (h.case_id,)).fetchone() == facts_before
        )
        async with api_db.request(scope_for(h.expert)) as conn:
            assert await record_entries.entries_ready(conn, h.case_id) is True
            assert await record_entries.entries_status(conn, h.case_id) == "ready"
        asked = len(bedrock.requests)
        await h.enqueue("extract_entries")
        await h.drain()
        assert len(bedrock.requests) == asked, "a case that has its entries is not asked again"
        assert admin.execute(
            "select count(*) from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        ).fetchone() == (2,)


def test_the_entries_job_needs_the_read_action(admin: psycopg.Connection) -> None:
    row = admin.execute("select action from app.job_kind_actions where kind = 'extract_entries'").fetchone()
    assert row == ("page.read",)


# Which pages count (review fixes) ---------------------------------------------------------------


def _analysed(
    admin: psycopg.Connection,
    case: uuid.UUID,
    page: uuid.UUID,
    *,
    read: bool,
    needs_human: bool = False,
    failed: bool = False,
    reason: str | None = None,
) -> None:
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, stage, extracted_at, needs_human, reason,"
        " entries_extracted_at, entries_failed_at)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'extract', now(), %s, %s,"
        " case when %s then now() end, case when %s then now() end)",
        (page, case, needs_human, reason, read, failed),
    )


async def test_a_quarantined_page_does_not_hold_the_case_back_and_no_empty_job_is_queued(
    monkeypatch: pytest.MonkeyPatch, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case)
    read, held = seed.page(case, doc, 1), seed.page(case, doc, 2)
    _analysed(admin, case, read, read=True)
    _analysed(admin, case, held, read=False)
    admin.execute("update app.pages set quarantined = true where id = %s", (held,))
    queued: list[Any] = []

    class Registry:
        async def enqueue(self, *args: Any, **kw: Any) -> None:
            queued.append(args)

    monkeypatch.setattr(record_entries, "registry", Registry())
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is True
        assert await record_entries.entries_status(conn, case) == "ready"
        assert await record_entries.request_entries(conn, case) is None
        assert await record_entries.request_entries(conn, case) is None
    assert queued == []


async def test_a_page_that_could_not_be_read_is_counted_and_does_not_block_ready(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case)
    read, failed = seed.page(case, doc, 1), seed.page(case, doc, 2)
    _analysed(admin, case, read, read=True)
    _analysed(admin, case, failed, read=False, needs_human=True, failed=True, reason="invalid_output")
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is True
        assert await record_entries.entries_unread_pages(conn, case) == [2]


async def test_a_failed_answer_leaves_the_page_unread_and_for_a_person(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    bedrock = ScriptedBedrock([response({"unexpected": "shape"})])  # never valid
    async with await opened(tmp_path, bedrock, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        await h.upload(document)
        assert set((await h.drain()).values()) == {"done"}
        (page,) = h.pages()
        row = admin.execute(
            "select entries_extracted_at is null, needs_human, reason from app.page_analysis where page_id = %s",
            (page["id"],),
        ).fetchone()
        assert row == (True, True, "invalid_output")
        assert admin.execute(
            "select count(*) from app.record_entries where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        ).fetchone() == (0,)
        async with api_db.request(scope_for(h.expert)) as conn:
            assert await record_entries.entries_unread_pages(conn, h.case_id) == [page["bundle_page_no"]]
            assert await record_entries.entries_ready(conn, h.case_id) is True


async def test_the_entries_job_asks_a_failing_page_twice_then_leaves_it_for_a_person(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    bedrock = ScriptedBedrock([response({"unexpected": "shape"})])
    async with await opened(tmp_path, bedrock, admin, api_db, worker_dsn, auth_env, seed, storage) as h:
        await h.upload(document)
        await h.drain()
        # A page extracted before entries existed: no flag, nothing read.
        admin.execute(
            "update app.page_analysis set needs_human = false, reason = null, entries_failed_at = null"
            " where bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        )
        asked = len(bedrock.requests)
        await h.enqueue("extract_entries")
        assert set((await h.drain()).values()) == {"done"}
        assert len(bedrock.requests) - asked == 2
        (page,) = h.pages()
        row = admin.execute(
            "select entries_extracted_at is null, needs_human, reason from app.page_analysis where page_id = %s",
            (page["id"],),
        ).fetchone()
        assert row == (True, True, "invalid_output")
        await h.enqueue("extract_entries")
        await h.drain()
        assert len(bedrock.requests) - asked == 2, "a page left for a person is not asked again"


async def test_an_older_page_flagged_by_fact_extraction_is_still_asked_for_entries(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    from app.features.pipeline import repo

    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case)
    older, failed = seed.page(case, doc, 1), seed.page(case, doc, 2)
    _analysed(admin, case, older, read=False, needs_human=True, reason="not_in_words")
    _analysed(admin, case, failed, read=False, needs_human=True, failed=True, reason="invalid_output")
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_unread_pages(conn, case) == [2]
        assert await record_entries.entries_ready(conn, case) is False
        assert await repo.pages_to_read_entries(conn, case) == [older]


async def test_a_case_part_way_through_the_pipeline_is_not_ready(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case)
    done, waiting, unseen = (seed.page(case, doc, n) for n in (1, 2, 3))
    _analysed(admin, case, done, read=True)
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is False  # pages 2 and 3 have no analysis
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, stage)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'ocr')",
        (waiting, case),
    )
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is False
    admin.execute("update app.pages set quarantined = true where id in (%s, %s)", (waiting, unseen))
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is True


async def test_a_case_with_every_page_set_aside_is_ready_and_queues_nothing(
    monkeypatch: pytest.MonkeyPatch, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    page = seed.page(case, seed.document(case), 1)
    _analysed(admin, case, page, read=False)
    admin.execute("update app.pages set quarantined = true where id = %s", (page,))
    queued: list[Any] = []

    class Registry:
        async def enqueue(self, *args: Any, **kw: Any) -> None:
            queued.append(args)

    monkeypatch.setattr(record_entries, "registry", Registry())
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_ready(conn, case) is True
        assert await record_entries.request_entries(conn, case) is None
    assert queued == []
