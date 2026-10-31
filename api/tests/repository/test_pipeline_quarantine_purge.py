"""Slice A increment 11b-2: a quarantined page's image and text are deleted (architecture 10.3,
state-machines.md "quarantined image and text", brief "deleted after the note is sent or 14 days").

- A daily schedule (`purge_quarantined_pages`) deletes the image and text of every confirmed
  quarantine once the instructing party has been told (a logged request lists the page in
  `quarantined_page_ids`) or once the pack's `quarantine_purge_days` have passed since it was
  confirmed. Its files in Storage, its words and what was read from it are deleted; the page keeps
  its number and stays quarantined. The quarantine becomes `purged`, audited once.
- Suggestions and undone quarantines are never deleted. Undo works only before the deletion: after
  it, `DELETE …/quarantine` is 409.
- A logged request may list only confirmed quarantines of the case (404 otherwise).
- Only the worker can run the deletion; the API's role cannot.
Runs under the launch and the fixture packs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_quarantine import (
    OTHER_PAGE,
    answer,
    audited,
    bundle,
    page_id,
    quarantine_path,
    summary,
)
from tests.repository.test_pipeline_support import Harness, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor, scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PURGE_QUARANTINED = "purge_quarantined_pages"  # the daily schedule's name


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
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
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    """Page 4 (another person's) is quarantined by the named expert."""
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    pack = default_library().combine(*combo.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([answer(pack)]),
    ) as running:
        await running.upload(text_pdf(bundle(combo.codes[0])))
        await running.drain()
        confirmed = await running.client.post(quarantine_path(running, OTHER_PAGE), json={"reason": "not_this_person"})
        assert confirmed.json()["status"] == "confirmed"
        running.days = pack.quarantine_purge_days  # type: ignore[attr-defined]
        yield running


async def purge(h: Harness, day: dt.date) -> None:
    async with h.worker_db.anonymous() as conn:
        await h.registry.schedules[PURGE_QUARANTINED](conn, day)


def confirmed_on(h: Harness) -> dt.date:
    row = h.admin.execute(
        "select (confirmed_at at time zone 'UTC')::date from app.page_quarantines"
        " where case_id = %s and confirmed_at is not null",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def files_of(h: Harness, n: int) -> list[str]:
    return await h.storage.list(paths.page_prefix(h.case_id, page_id(h, n)))


def text_of(h: Harness, n: int) -> dict[str, Any]:
    pid = page_id(h, n)
    row = h.admin.execute(
        "select (select count(*) from app.ocr_words w where w.page_id = p.id),"
        " a.extraction is not null, a.minhash is not null, cardinality(a.printed_dates), p.image_path,"
        " p.quarantined, p.bundle_page_no"
        " from app.pages p left join app.page_analysis a on a.page_id = p.id where p.id = %s",
        (pid,),
    ).fetchone()
    assert row is not None
    keys = ("words", "extraction", "minhash", "printed_dates", "image_path", "quarantined", "page_no")
    return dict(zip(keys, row, strict=True))


def status_of(h: Harness, n: int) -> list[str]:
    return [
        r[0]
        for r in h.admin.execute(
            "select status from app.page_quarantines where page_id = %s order by created_at", (page_id(h, n),)
        )
    ]


async def test_a_quarantined_page_is_deleted_once_the_packs_days_have_passed(h: Harness) -> None:
    days: int = h.days  # type: ignore[attr-defined]
    start = confirmed_on(h)
    assert await files_of(h, OTHER_PAGE), "the page had no files to delete"
    assert text_of(h, OTHER_PAGE)["words"] > 0

    await purge(h, start + dt.timedelta(days=days - 1))
    assert status_of(h, OTHER_PAGE) == ["confirmed"], "deleted before the pack's days had passed"
    assert await files_of(h, OTHER_PAGE)

    await purge(h, start + dt.timedelta(days=days))
    assert status_of(h, OTHER_PAGE) == ["purged"]
    assert await files_of(h, OTHER_PAGE) == [], "the page's image was kept"
    assert text_of(h, OTHER_PAGE) == {
        "words": 0,
        "extraction": False,
        "minhash": False,
        "printed_dates": 0,
        "image_path": None,
        "quarantined": True,
        "page_no": OTHER_PAGE,
    }, "the page's text was kept, or it lost its number"
    assert await files_of(h, 1), "another page's files were deleted"
    assert text_of(h, 1)["words"] > 0
    assert audited(h, "page.quarantine_purged") == 1
    purged_at = h.admin.execute(
        "select purged_at from app.page_quarantines where page_id = %s", (page_id(h, OTHER_PAGE),)
    ).fetchone()
    assert purged_at is not None and purged_at[0] is not None

    await purge(h, start + dt.timedelta(days=days + 1))
    assert audited(h, "page.quarantine_purged") == 1, "a deleted page was deleted again"

    assert (await h.client.delete(quarantine_path(h, OTHER_PAGE))).status_code == 409
    got = await summary(h)
    assert [(q["page_no"], q["status"]) for q in got["quarantined"]] == [(OTHER_PAGE, "purged")]


async def test_telling_the_instructing_party_lets_the_page_go_on_the_next_run(h: Harness) -> None:
    today = confirmed_on(h)
    await purge(h, today)
    assert status_of(h, OTHER_PAGE) == ["confirmed"]

    request = {"subject": "Synthetic subject", "body": "Synthetic body"}
    wrong = await h.client.post(
        f"/v1/cases/{h.case_id}/instructing-party-requests",
        json={**request, "quarantined_page_ids": [str(page_id(h, 1))]},
    )
    assert wrong.status_code == 404, "a request listed a page that is not quarantined"
    plain = await h.client.post(f"/v1/cases/{h.case_id}/instructing-party-requests", json=request)
    assert plain.status_code == 201
    await purge(h, today)
    assert status_of(h, OTHER_PAGE) == ["confirmed"], "a request that did not list the page released it"

    sent = await h.client.post(
        f"/v1/cases/{h.case_id}/instructing-party-requests",
        json={**request, "quarantined_page_ids": [str(page_id(h, OTHER_PAGE))]},
    )
    assert sent.status_code == 201, sent.status_code
    listed = h.admin.execute(
        "select quarantined_page_ids from app.instructing_party_requests where id = %s", (sent.json()["id"],)
    ).fetchone()
    assert listed == ([page_id(h, OTHER_PAGE)],)
    await purge(h, today)
    assert status_of(h, OTHER_PAGE) == ["purged"]
    assert await files_of(h, OTHER_PAGE) == []
    assert text_of(h, OTHER_PAGE)["words"] == 0


async def test_only_confirmed_quarantines_are_ever_deleted(h: Harness, seed: Seed, client_for: ClientFor) -> None:
    days: int = h.days  # type: ignore[attr-defined]
    assistant = seed.person()
    seed.case_member(h.case_id, assistant, "assistant", added_by=h.expert)
    helper = await client_for(assistant)
    assert (await helper.post(quarantine_path(h, 5), json={"reason": "not_this_person"})).status_code == 200
    assert (await h.client.delete(quarantine_path(h, OTHER_PAGE))).status_code == 200
    await h.drain()

    await purge(h, confirmed_on(h) + dt.timedelta(days=days + 30))
    assert status_of(h, OTHER_PAGE) == ["undone"]
    assert status_of(h, 5) == ["suggested"]
    for n in (OTHER_PAGE, 5):
        assert await files_of(h, n), f"page {n} was deleted without a confirmed quarantine"
        assert text_of(h, n)["words"] > 0
    assert audited(h, "page.quarantine_purged") == 0


async def test_only_the_worker_can_delete_a_page(h: Harness) -> None:
    (quarantine_id,) = h.admin.execute(
        "select id from app.page_quarantines where case_id = %s", (h.case_id,)
    ).fetchone()  # type: ignore[misc]
    async with h.api_db.request(scope_for(h.expert)) as conn:
        for call in ("app.purge_quarantined_page(%s)", "app.quarantines_to_purge(current_date)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(f"select * from {call}", (quarantine_id,) if "%s" in call else ())
    h.admin.execute("update app.page_quarantines set status = 'undone' where id = %s", (quarantine_id,))
    async with h.worker_db.anonymous() as conn:
        with pytest.raises(psycopg.Error) as refused:
            async with conn.transaction():
                await conn.execute("select app.purge_quarantined_page(%s)", (quarantine_id,))
    assert refused.value.sqlstate == "AV409", "a quarantine that is not confirmed was deleted"
