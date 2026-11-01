"""Migration 0039 enabled RLS on app.legal_agreements with only a read policy; a grant without a
matching policy denies every write, so accept_legal() could never actually record an acceptance.
Migration 0046 adds the missing insert policy."""

from __future__ import annotations

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_a_person_can_insert_their_own_acceptance(
    api_db: Database, seed: Seed, admin: psycopg.Connection
) -> None:
    me = seed.person()
    async with api_db.request(scope_for(me)) as conn:
        await conn.execute(
            "insert into app.legal_agreements (person_id, document, version) values (%s, %s, %s)",
            (me, "terms", "2026-11-01.1"),
        )
    row = admin.execute("select person_id, document from app.legal_agreements where person_id = %s", (me,)).fetchone()
    assert row == (me, "terms")


async def test_a_person_cannot_insert_an_acceptance_for_someone_else(
    api_db: Database, seed: Seed, admin: psycopg.Connection
) -> None:
    me, other = seed.person(), seed.person()
    async with api_db.request(scope_for(me)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "insert into app.legal_agreements (person_id, document, version) values (%s, %s, %s)",
                (other, "terms", "2026-11-01.1"),
            )
    row = admin.execute("select 1 from app.legal_agreements where person_id = %s", (other,)).fetchone()
    assert row is None
