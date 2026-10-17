"""The worker knows every slice's jobs and has a mailer (slice H's deletion job sends the certificate).

- `build_worker()` imports every package under `app.features` and their `jobs` modules, so job kinds
  and schedules registered there are known to the worker.
- A running job reaches the mailer as `ctx.mailer`; the worker's default is the local outbox until
  the cell has its email service.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from app.core.db import Database
from app.core.email import NOTIFICATION_NEW, Mail, OutboxMailer
from app.core.jobs import JobContext
from tests.support.features import dummy_features
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

ENV = {
    "DATABASE_URL": "postgresql://app_api@db.invalid/postgres",
    "DATABASE_URL_DIRECT": "postgresql://app_worker@db.invalid/postgres",
}


def test_the_worker_imports_every_features_jobs(tmp_path: Path) -> None:
    from app.worker.main import build_worker

    features = dummy_features(tmp_path)
    worker = build_worker(ENV, features=features)
    assert f"{features}.dummy.jobs" in sys.modules
    assert f"{features}.quiet" in sys.modules
    assert isinstance(worker.mailer, OutboxMailer)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.anyio
async def test_a_job_sends_mail_through_the_workers_mailer(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    mailer = OutboxMailer()

    @registry.kind("zz_mail")
    async def zz_mail(ctx: JobContext) -> None:
        await ctx.mailer.send(Mail("expert@example.test", NOTIFICATION_NEW, {}))

    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    async with worker_db.job(case) as conn:
        job: uuid.UUID = await registry.enqueue(conn, case, "zz_mail")
    async with open_worker(registry, worker_db, worker_dsn, mailer=mailer) as worker:  # type: ignore[arg-type]
        assert await run_until_finished(worker, admin, [job]) == {job: "done"}
    assert [m.template for m in mailer.outbox] == [NOTIFICATION_NEW]
