"""Scenario S3: a new named expert starts with no reads and no checked facts; the suggestions stay.

Architecture 10.1 "Reassignment": reads and verifications are personal. Reads already are (only the
named expert's reads count for citing). Checks live on the fact itself (`facts.status`), which every
slice reads, so when `cases.named_expert_id` changes the database puts every checked fact (verified,
edited, rejected) back to `suggested` (migration 0031, with `facts_guard` in 0209):
- an edited fact gets back the suggestion it had before the outgoing expert changed it (value,
  fields, cited words and their box), unless its page was read again or it moved since then;
- each reset appends a `fact_verifications` row (`reset`, no person), so the history and its hash chain
  stay whole;
- a "Please check" flag goes with the check it asked about.
The new expert then reads and checks for themselves; only their checks make a fact citable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.core.contracts.facts import citable_facts
from app.core.db import Database
from app.features.reader.log import ReaderPageReadLog
from tests.fixtures.stub_facts import PAGE_HEIGHT, PAGE_WIDTH, load_stub_facts
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@dataclass
class Desk:
    owner: uuid.UUID
    dr_k: uuid.UUID
    dr_m: uuid.UUID
    case: uuid.UUID
    page: uuid.UUID
    verified: uuid.UUID
    edited: uuid.UUID
    rejected: uuid.UUID
    suggested: uuid.UUID
    original: dict[str, Any]  # the edited fact's suggestion before Dr K changed it


async def check(
    api_db: Database, person: uuid.UUID, case: uuid.UUID, fact: uuid.UUID, action: str, status: str, **change: Any
) -> None:
    """A check as slice B records it: the fact changes, then one `fact_verifications` row."""
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute("select value, fields, word_ids from app.facts where id = %s", (fact,))
        before = await cur.fetchone()
        assert before is not None
        value, word_ids = change.get("value", before[0]), change.get("word_ids", before[2])
        await conn.execute(
            "update app.facts set status = %s, value = %s, word_ids = %s, bbox = coalesce(%s, bbox) where id = %s",
            (status, value, word_ids, Jsonb(change["bbox"]) if "bbox" in change else None, fact),
        )
        await conn.execute(
            """insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,
                   old_fields, new_fields, old_word_ids, new_word_ids)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (case, fact, person, action, before[0], value, Jsonb(before[1]), Jsonb(before[1]), before[2], word_ids),
        )


async def read(api_db: Database, person: uuid.UUID, case: uuid.UUID, page: uuid.UUID) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await conn.execute(
            "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 30)",
            (case, page, person),
        )


async def reassign(api_db: Database, owner: uuid.UUID, case: uuid.UUID, to: uuid.UUID) -> None:
    """As slice O's route does it: the organisation owner changes `cases.named_expert_id`."""
    async with api_db.request(scope_for(owner)) as conn:
        await conn.execute("update app.cases set named_expert_id = %s where id = %s", (to, case))


async def citable(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> set[uuid.UUID]:
    async with api_db.request(scope_for(person)) as conn:
        return {f.id for f in await citable_facts(conn, case, ReaderPageReadLog())}


def fact_row(admin: psycopg.Connection, fact: uuid.UUID) -> dict[str, Any]:
    row = admin.execute(
        "select status, value, fields, word_ids, bbox, version from app.facts where id = %s", (fact,)
    ).fetchone()
    assert row is not None
    return dict(zip(("status", "value", "fields", "word_ids", "bbox", "version"), row, strict=True))


@pytest.fixture
async def desk(seed: Seed, api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection) -> Desk:
    """Dr K has read page 118 and checked four of its facts (one each way) before the case moves to Dr M."""
    owner, dr_k, dr_m = seed.person(), seed.person(), seed.person()
    org = seed.org(kind="team", members={owner: "owner", dr_k: "expert", dr_m: "expert"})
    case = seed.case(org, dr_k, status="active")
    loaded = await load_stub_facts(aconn, case, pages=[118])
    page = loaded[118].page_id
    # Slice A records the page's size in its words' units, which boxes are drawn against.
    admin.execute(
        "insert into app.page_analysis (page_id, case_id, width, height) values (%s, %s, %s, %s)",
        (page, case, PAGE_WIDTH, PAGE_HEIGHT),
    )
    verified, edited, rejected, suggested, other, *_ = loaded[118].fact_ids
    original = fact_row(admin, edited)
    other_row = fact_row(admin, other)
    await read(api_db, dr_k, case, page)
    await check(api_db, dr_k, case, verified, "correct", "verified")
    # "Change a detail": Dr K makes the fact cite other words on the page, which show another value.
    await check(
        api_db, dr_k, case, edited, "edit", "edited",
        value=other_row["value"], word_ids=other_row["word_ids"], bbox=other_row["bbox"],
    )  # fmt: skip
    await check(api_db, dr_k, case, rejected, "not_right", "rejected")
    return Desk(owner, dr_k, dr_m, case, page, verified, edited, rejected, suggested, original)


async def test_before_reassignment_the_outgoing_experts_checks_are_citable(api_db: Database, desk: Desk) -> None:
    assert await citable(api_db, desk.dr_k, desk.case) == {desk.verified, desk.edited}


async def test_the_new_expert_starts_with_every_fact_a_suggestion(
    api_db: Database, admin: psycopg.Connection, desk: Desk
) -> None:
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    statuses = admin.execute("select distinct status from app.facts where case_id = %s", (desk.case,)).fetchall()
    assert statuses == [("suggested",)]


async def test_an_edited_fact_gets_its_suggestion_back(api_db: Database, admin: psycopg.Connection, desk: Desk) -> None:
    before = fact_row(admin, desk.edited)
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    after = fact_row(admin, desk.edited)
    for key in ("value", "fields", "word_ids"):
        assert after[key] == desk.original[key], key
    # The box is drawn again around the restored words, to 5 decimal places as slice B draws boxes.
    assert after["bbox"] == pytest.approx(desk.original["bbox"], abs=1e-5)
    assert after["version"] > before["version"]  # an ETag held from before the reset is stale


async def test_nothing_is_citable_until_the_new_expert_reads_and_checks(api_db: Database, desk: Desk) -> None:
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    assert await citable(api_db, desk.dr_m, desk.case) == set()
    await read(api_db, desk.dr_m, desk.case, desk.page)
    assert await citable(api_db, desk.dr_m, desk.case) == set(), "Dr K's checks do not carry over"
    await check(api_db, desk.dr_m, desk.case, desk.rejected, "correct", "verified")
    assert await citable(api_db, desk.dr_m, desk.case) == {desk.rejected}


async def test_each_reset_is_in_the_verification_log_and_the_chain_holds(
    api_db: Database, admin: psycopg.Connection, desk: Desk
) -> None:
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    rows = admin.execute(
        "select fact_id, person_id, old_value, new_value from app.fact_verifications"
        " where case_id = %s and action = 'reset' order by seq",
        (desk.case,),
    ).fetchall()
    assert {r[0] for r in rows} == {desk.verified, desk.edited, desk.rejected}
    assert all(r[1] is None for r in rows)
    edited = next(r for r in rows if r[0] == desk.edited)
    assert edited[3] == desk.original["value"]
    assert admin.execute("select app.fact_verifications_chain_ok(%s)", (desk.case,)).fetchone() == (True,)


async def test_a_please_check_flag_goes_with_the_check(
    api_db: Database, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'recheck', %s)",
        (desk.case, desk.verified, desk.dr_k),
    )
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    flags = admin.execute(
        "select 1 from app.fact_flags where fact_id = %s and flagged_for = 'recheck'", (desk.verified,)
    )
    assert flags.fetchall() == []


async def test_an_edit_whose_page_was_read_again_keeps_its_current_words(
    api_db: Database, admin: psycopg.Connection, desk: Desk
) -> None:
    """The suggestion's own words may be gone after a re-read, so the fact keeps the words it cites now."""
    current = fact_row(admin, desk.edited)
    admin.execute(
        """insert into app.fact_verifications (case_id, fact_id, person_id, action, old_word_ids, new_word_ids)
           values (%s, %s, null, 'remapped', %s, %s)""",
        (desk.case, desk.edited, current["word_ids"], current["word_ids"]),
    )
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    after = fact_row(admin, desk.edited)
    assert (after["status"], after["value"], after["word_ids"]) == ("suggested", current["value"], current["word_ids"])


async def test_a_second_reassignment_restores_the_same_suggestion(
    api_db: Database, admin: psycopg.Connection, seed: Seed, desk: Desk
) -> None:
    await reassign(api_db, desk.owner, desk.case, desk.dr_m)
    await read(api_db, desk.dr_m, desk.case, desk.page)
    await check(api_db, desk.dr_m, desk.case, desk.edited, "edit", "edited", value="changed by Dr M")
    await reassign(api_db, desk.owner, desk.case, desk.dr_k)
    after = fact_row(admin, desk.edited)
    assert (after["status"], after["value"]) == ("suggested", desk.original["value"])


async def test_the_running_app_cannot_put_a_checked_fact_back_by_itself(api_db: Database, desk: Desk) -> None:
    """Only the reassignment resets checks: setting the reset marker by hand opens nothing."""
    async with api_db.request(scope_for(desk.dr_k)) as conn:
        await conn.execute("select set_config('app.fact_checks_reset', %s, true)", (str(desk.case),))
        with pytest.raises(psycopg.Error) as err:
            await conn.execute("update app.facts set status = 'suggested' where id = %s", (desk.verified,))
    assert err.value.sqlstate == "AV409"
