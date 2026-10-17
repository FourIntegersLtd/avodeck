"""The instructing-party contract (`app.core.contracts.instructing_parties`) slice H reads (cross-slice.md).

- `instructing_party_contacts`: who instructs the case and their email, for the certificate of deletion
  (read before the purge). Current parties only by default; the full history on request.
- `instructing_party_requests`: every set of instructions (letters, supplementary letters, oral
  instructions with their words), oldest first, for the export.
Row-level security still applies: a person outside the case gets nothing; the worker reads in the case's
own job.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from app.core.contracts.instructing_parties import (
    ORAL_INSTRUCTION_PURPOSE,
    instructing_party_contacts,
    instructing_party_requests,
)
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, outsider = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active")
    old = admin.execute(
        "insert into app.case_instructing_parties (case_id, type_key, name, firm, email, effective_from, effective_to)"
        " values (%s, 'zz_firm', 'Old Contact', 'Old Firm', 'old@example.test', '2026-01-01', '2026-02-01')"
        " returning id",
        (case,),
    ).fetchone()
    assert old is not None
    for name, firm, email in (("Ann Zed", "Zed Firm", "ann@example.test"), ("Bo Why", "Why Firm", None)):
        admin.execute(
            "insert into app.case_instructing_parties (case_id, type_key, name, firm, email, effective_from)"
            " values (%s, 'zz_firm', %s, %s, %s, '2026-02-02')",
            (case, name, firm, email),
        )
    return {"expert": expert, "outsider": outsider, "case": case}


async def test_contacts_are_the_current_instructing_parties_with_their_email(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        current = await instructing_party_contacts(conn, world["case"])
        everyone = await instructing_party_contacts(conn, world["case"], current_only=False)
    assert [(c.name, c.firm, c.email) for c in current] == [
        ("Ann Zed", "Zed Firm", "ann@example.test"),
        ("Bo Why", "Why Firm", None),
    ]
    assert all(c.current for c in current)
    assert [c.name for c in everyone] == ["Old Contact", "Ann Zed", "Bo Why"]
    assert not everyone[0].current and everyone[0].effective_to == date(2026, 2, 1)


async def test_people_outside_the_case_get_no_contacts(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await instructing_party_contacts(conn, world["case"], current_only=False) == []


async def test_the_worker_reads_contacts_in_the_case_job(worker_dsn: str, world: dict[str, uuid.UUID]) -> None:
    db = Database(worker_dsn, max_size=1)
    await db.open()
    try:
        async with db.job(world["case"]) as conn:
            assert len(await instructing_party_contacts(conn, world["case"])) == 2
    finally:
        await db.close()


async def test_requests_list_every_set_of_instructions_with_the_oral_words(
    api_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    crypto = CaseCrypto(LocalKeyProvider.generate())
    doc = seed.document(case)
    letter = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind, document_id, received_on)"
        " values (%s, 1, 'letter', %s, '2026-02-03') returning id",
        (case, doc),
    ).fetchone()
    oral = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind, received_on)"
        " values (%s, 2, 'oral', '2026-03-04') returning id",
        (case,),
    ).fetchone()
    assert letter is not None and oral is not None
    async with api_db.request(scope_for(world["expert"])) as conn:
        sealed = await crypto.encrypt(
            conn, case, b"Please also look at the zz notes.", purpose=ORAL_INSTRUCTION_PURPOSE
        )
    admin.execute(
        "insert into app.case_instructions (case_id, instruction_version_id, text, source, received_on)"
        " values (%s, %s, %s, 'typed', '2026-03-04')",
        (case, oral[0], sealed),
    )

    async with api_db.request(scope_for(world["expert"])) as conn:
        found = await instructing_party_requests(conn, case, crypto)
    assert [(r.version_no, r.kind, r.document_id, r.received_on, r.text, r.source) for r in found] == [
        (1, "letter", doc, date(2026, 2, 3), None, None),
        (2, "oral", None, date(2026, 3, 4), "Please also look at the zz notes.", "typed"),
    ]
    assert [r.instruction_version_id for r in found] == [letter[0], oral[0]]

    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await instructing_party_requests(conn, case, crypto) == []
