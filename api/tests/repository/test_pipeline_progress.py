"""Slice A increment 7: progress steps and "bundle ready" (architecture 7, S07, behaviour index `jobs`).

- `process_bundle` records exactly the five steps the Preparing screen shows (split, ocr, sort,
  duplicates, missing), in that order, and ends at 100%.
- Job rows and logs carry step codes and numbers only: no word from any page (rule 6).
- When new pages are ready the named expert is told once through `core.notifications.notify()`
  with kind `bundle_ready`; the email carries the kind only. Running the job again tells no one.
- A case waiting in `processing` becomes `active` when its bundle is ready.
Runs under the launch and the fixture packs; the files are made up here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, empty_answer, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

MARKER = "Quillbrook"
PAGES = [[f"{MARKER} synthetic page one"], [f"{MARKER} synthetic page two"], [f"{MARKER} synthetic three"]]
PUBLIC_STEPS = ["split", "ocr", "sort", "duplicates", "missing"]


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
    combo: Combo = request.param
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case_id = seed.case(org, expert, packs=combo.case_row(), status="processing")
    for channel in ("in_app", "email"):
        admin.execute(
            "insert into app.notification_preferences (person_id, organisation_id, channel, enabled, digest,"
            " muted_kinds) values (%s, %s, %s, true, false, '{}')",
            (expert, org, channel),
        )
    first_source = default_library().combine(*combo.codes).source_types()[0]["key"]
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer(first_source)]),
    ) as running:
        running.mailer = OutboxMailer()  # type: ignore[attr-defined]
        from app.features.pipeline.wiring import current

        current().mailer = running.mailer  # type: ignore[attr-defined]
        yield running


def bundle_job(h: Harness) -> uuid.UUID:
    row = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind = 'process_bundle' order by created_at limit 1",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def notices(h: Harness) -> list[tuple[object, ...]]:
    return h.admin.execute(
        "select kind, case_id from app.notifications where person_id = %s order by created_at", (h.expert,)
    ).fetchall()


async def test_the_five_steps_are_recorded_in_order_and_the_job_ends_at_100(h: Harness) -> None:
    await h.upload(text_pdf(PAGES))
    await h.drain()
    job = bundle_job(h)
    steps = [
        r[0] for r in h.admin.execute("select step from app.job_steps where job_id = %s order by created_at", (job,))
    ]
    assert steps == PUBLIC_STEPS
    status, pct, step = h.admin.execute("select status, pct, step from app.jobs where id = %s", (job,)).fetchone()  # type: ignore[misc]
    assert (status, pct, step) == ("done", 100, "missing")


async def test_jobs_and_logs_hold_no_page_words(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await h.upload(text_pdf(PAGES))
    await h.drain()
    rows = h.admin.execute(
        "select coalesce(step, ''), coalesce(error_code, '') from app.jobs where case_id = %s", (h.case_id,)
    ).fetchall()
    held = " ".join(str(v) for row in rows for v in row) + " ".join(
        r[0] for r in h.admin.execute("select step from app.job_steps where case_id = %s", (h.case_id,))
    )
    assert MARKER.casefold() not in held.casefold()
    assert not any(MARKER.casefold() in r.getMessage().casefold() for r in caplog.records)


async def test_the_named_expert_is_told_once_in_the_app_and_not_by_email(h: Harness) -> None:
    await h.upload(text_pdf(PAGES))
    await h.drain()
    assert notices(h) == [("bundle_ready", h.case_id)]
    outbox = h.mailer.outbox  # type: ignore[attr-defined]
    assert outbox == [], "stage news stays in the app; it is not emailed"

    await h.enqueue("process_bundle")
    await h.drain()
    assert len(notices(h)) == 1
    assert outbox == []


async def test_a_processing_case_becomes_active_when_the_bundle_is_ready(h: Harness) -> None:
    await h.upload(text_pdf(PAGES))
    await h.drain()
    status = h.admin.execute("select status from app.cases where id = %s", (h.case_id,)).fetchone()
    assert status == ("active",)
