"""`identity_for`: the one answer to "what must be stripped from this case before a model call" (rule 7).

Merges the parties' names (slice C `case_parties`) with the subject's name, other names and date of birth
(slice C `case_subject_identity`, ADR 0026), each name followed by its parts. Runs under row-level security.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest

from app.core.contracts import case_identity
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.setup import identity as setup_identity
from tests.support import identity as fx
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, outsider = seed.person(), seed.person()
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    return {"expert": expert, "outsider": outsider, "case": case}


def test_the_purposes_match_the_ones_setup_seals_with() -> None:
    assert case_identity.SUBJECT_NAME == setup_identity.SUBJECT_NAME
    assert case_identity.SUBJECT_OTHER_NAMES == setup_identity.SUBJECT_OTHER_NAMES
    assert case_identity.SUBJECT_DOB == setup_identity.SUBJECT_DOB


async def test_the_subject_and_the_parties_merge(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    await fx.record_identity(aconn, CRYPTO, world["case"])
    async with api_db.request(scope_for(world["expert"])) as conn:
        who = await case_identity.identity_for(conn, CRYPTO, world["case"])
    assert list(who.dates_of_birth) == [date(1960, 2, 1)]
    assert set(who.names) == {
        "Zebedee Quillfeather",
        "Zebedee",
        "Quillfeather",
        "Jane Testperson",
        "Jane",
        "Testperson",
        "Jane Oldname",
        "Oldname",
    }
    assert len(who.names) == len(set(who.names)), "no name twice"


async def test_a_case_with_no_identity_row_gives_the_parties_only(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    sealed = await CRYPTO.encrypt(aconn, world["case"], b"Al Bo", purpose="party_name")
    await aconn.execute(
        "insert into app.case_parties (case_id, role_key, name, position) values (%s, 'party_a', %s, 0)",
        (world["case"], sealed),
    )
    async with api_db.request(scope_for(world["expert"])) as conn:
        who = await case_identity.identity_for(conn, CRYPTO, world["case"])
    assert list(who.names) == ["Al Bo"]
    assert list(who.dates_of_birth) == []


async def test_a_subject_with_no_date_of_birth_or_other_names(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    await setup_identity.save_identity(
        aconn, CRYPTO, world["case"], name="Jane Testperson", other_names=[], date_of_birth=None
    )
    async with api_db.request(scope_for(world["expert"])) as conn:
        who = await case_identity.identity_for(conn, CRYPTO, world["case"])
    assert list(who.names) == ["Jane Testperson", "Jane", "Testperson"]
    assert list(who.dates_of_birth) == []


async def test_someone_without_access_gets_nothing(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    await fx.record_identity(aconn, CRYPTO, world["case"])
    async with api_db.request(scope_for(world["outsider"])) as conn:
        who = await case_identity.identity_for(conn, CRYPTO, world["case"])
    assert list(who.names) == [] and list(who.dates_of_birth) == []
