"""Rule 7 for slice A: the subject's name, other name and date of birth never reach T-07b.

The pipeline is built as the cell builds it (identity from `identity_for`), the case has a recorded subject,
and the page types the name, the other name and the date of birth in three forms. The request that would
leave the cell holds none of them, and the page's own words are still there.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, empty_answer, harness, t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.identity import leaked, record_identity
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SOURCE = default_library().combine(*LAUNCH.codes).source_types()[0]["key"]
LINES = [
    "Jane Testperson",
    "also Jane Oldname",
    "born 01/02/1960",
    "again 1 February 1960",
    "or 01 Feb 1960",
    "Seen with Zebedee Quillfeather",
    "Synthetic chest review",
]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer(SOURCE)]),
        wired_identity=True,
    ) as running:
        yield running


async def test_t07b_never_carries_the_subject_or_the_parties(h: Harness, aconn: psycopg.AsyncConnection) -> None:
    await record_identity(aconn, h.crypto, h.case_id)
    await h.upload(text_pdf([LINES]))
    finished = await h.drain()
    assert set(finished.values()) == {"done"}, finished
    sent = t07b_requests(h)
    assert sent, "the page was sent to the model"
    assert leaked([r.text for r in sent]) == []
    assert any("chest" in r.text for r in sent), "the page's own words still go"
