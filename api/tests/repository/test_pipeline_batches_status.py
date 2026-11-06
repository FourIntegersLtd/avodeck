"""The screen follows the bundle job, not a job waiting its turn (ADR 0038, build step 8b).

A job paused `busy_processing` (`retry_unread_pages`, `reprocess_case`, ... waiting for a page-batch run to end) has no
queue row and is handed back later. `repo.latest_processing` skips it, so S07 keeps showing the running parent instead
of a paused panel in the gap before the hand-back. Any other pause is still shown. Ids and codes only (rule 6).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.contracts.record_entries import RETRY_UNREAD
from app.core.db import Database
from app.features.pipeline import repo
from tests.repository.test_pipeline_batches_support import make_job
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

KINDS = ("scan_document", "process_bundle", "process_pages", "reprocess_case")


async def latest(api_db: Database, expert: uuid.UUID, case: uuid.UUID) -> dict[str, Any] | None:
    async with api_db.request(scope_for(expert)) as conn:
        row = await repo.latest_processing(conn, case, KINDS)
    return None if row is None else dict(row)


def new_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(members={expert: "owner"}), expert)


async def test_a_job_paused_busy_is_not_the_one_the_screen_follows(
    admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = new_case(seed)
    parent = make_job(admin, seed, case, status="running")
    make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    found = await latest(api_db, expert, case)
    assert found is not None
    assert found["id"] == parent


async def test_a_case_with_only_a_job_paused_busy_has_nothing_to_follow(
    admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = new_case(seed)
    make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    assert await latest(api_db, expert, case) is None


@pytest.mark.parametrize("code", ["service_busy", "model_unavailable", "cancelled"])
async def test_any_other_pause_is_still_shown(
    admin: psycopg.Connection, api_db: Database, seed: Seed, code: str
) -> None:
    expert, case = new_case(seed)
    paused = make_job(admin, seed, case, status="paused", code=code)
    found = await latest(api_db, expert, case)
    assert found is not None
    assert found["id"] == paused
    assert found["error_code"] == code


async def test_the_newest_job_is_followed_when_nothing_is_running(
    admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = new_case(seed)
    make_job(admin, seed, case, status="done")
    newer = make_job(admin, seed, case, status="failed")
    make_job(admin, seed, case, kind="reprocess_case", status="paused", code="busy_processing")
    found = await latest(api_db, expert, case)
    assert found is not None
    assert found["id"] == newer


async def test_a_retry_waiting_for_the_bundle_counts_as_running_not_paused(
    admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    """The summary's "Try these pages again" progress: a retry paused `busy_processing` is only waiting its turn."""
    expert, case = new_case(seed)
    make_job(admin, seed, case, kind=RETRY_UNREAD, status="paused", code="busy_processing")
    async with api_db.request(scope_for(expert)) as conn:
        progress = await repo.retry_progress(conn, case)
    assert progress is not None
    assert progress.running
    assert not progress.paused
    assert progress.pause_reason is None


async def test_a_retry_paused_for_another_reason_is_still_paused(
    admin: psycopg.Connection, api_db: Database, seed: Seed
) -> None:
    expert, case = new_case(seed)
    make_job(admin, seed, case, kind=RETRY_UNREAD, status="paused", code="cost_guard")
    async with api_db.request(scope_for(expert)) as conn:
        progress = await repo.retry_progress(conn, case)
    assert progress is not None
    assert progress.paused
    assert progress.pause_reason == "cost_guard"
