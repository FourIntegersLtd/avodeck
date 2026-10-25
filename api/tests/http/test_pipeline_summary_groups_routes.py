"""Slice A: the bundle summary hands the screens ready-made groups (rule 4: the browser filters nothing).

- `unopened_documents` / `unopened_count`: files that could not be opened (locked or damaged).
- `quarantine_listed`: the pages screen 30 shows: open suggestions, then confirmed or purged pages.
- `quarantine_confirmed`: confirmed pages only (the ones a request to the instructing party leaves out).
- `suggested_count` and `set_aside_count` count the suggestions and the pages set aside.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
    ) as running:
        yield running


def held(h: Harness, doc: uuid.UUID, number: int, status: str) -> None:
    pid = Seed(h.admin).page(h.case_id, doc, number)
    h.admin.execute(
        "insert into app.page_quarantines (case_id, page_id, status, reason) values (%s, %s, %s, 'identifiers_differ')",
        (h.case_id, pid, status),
    )


async def test_the_summary_groups_what_the_screens_show(h: Harness) -> None:
    seed = Seed(h.admin)
    doc = seed.document(h.case_id)
    for n, status in ((1, "suggested"), (2, "confirmed"), (3, "purged"), (4, "dismissed")):
        held(h, doc, n, status)
    locked = seed.document(h.case_id)
    h.admin.execute("update app.documents set unreadable_reason = 'locked' where id = %s", (locked,))

    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert got.status_code == 200, got.status_code
    s = got.json()
    assert [d["id"] for d in s["unopened_documents"]] == [str(locked)] and s["unopened_count"] == 1
    assert [(q["page_no"], q["status"]) for q in s["quarantine_listed"]] == [
        (1, "suggested"),
        (2, "confirmed"),
        (3, "purged"),
    ]
    assert [q["page_no"] for q in s["quarantine_confirmed"]] == [2]
    assert (s["suggested_count"], s["set_aside_count"]) == (1, 2)
