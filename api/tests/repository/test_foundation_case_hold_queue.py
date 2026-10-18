"""A case on hold (state-machines.md "On hold"; architecture 10 "On hold"; request from slice C).

- The worker does not run a job of a case on hold: the job stays `queued` with `error_code`
  `case_on_hold`. A kind registered with `runs_on_hold=True` (it deals with the hold itself, like the
  deletion run that tells the expert it is paused) still runs.
- Messages about a case on hold are kept back: not listed in the app, not emailed at once and not in
  the "one email a day" email. `notify(..., while_on_hold=True)` is for a message about the hold itself.
- `release_case_hold()` (when the case resumes) puts its held jobs back on the queue and releases its
  messages: listed again, emailed at once to the people who choose that, the rest in the daily email.
- Only someone who may resume the case (`case.hold`) releases it, and only once it is off hold.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest

from app.core.case_hold import release_case_hold
from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.jobs import JobContext, JobWorker
from app.core.notifications import notify, send_daily
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    t = {n: seed.person() for n in ("expert", "assistant", "viewer")}
    t["org"] = seed.org(
        kind="team", members={t["expert"]: "expert", t["assistant"]: "assistant", t["viewer"]: "viewer"}
    )
    t["case"] = seed.case(
        t["org"], t["expert"], status="active", members={t["assistant"]: "assistant", t["viewer"]: "viewer"}
    )
    return t


def hold(admin: psycopg.Connection, case: uuid.UUID, on_hold: bool = True) -> None:
    admin.execute("update app.cases set on_hold = %s where id = %s", (on_hold, case))


def job_row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[str, str | None]:
    row = admin.execute("select status, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return row[0], row[1]


async def run_until_held(worker: JobWorker, admin: psycopg.Connection, job: uuid.UUID, timeout: float = 20) -> None:
    task = asyncio.create_task(worker.run(concurrency=1))
    deadline = time.monotonic() + timeout
    try:
        while job_row(admin, job)[1] != "case_on_hold":
            if task.done():
                task.result()
                raise AssertionError("worker stopped early")
            if time.monotonic() > deadline:
                raise AssertionError(f"job not held in {timeout}s: {job_row(admin, job)}")
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)  # long enough for a wrongly run job to have started
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def release(api_db: Database, person: uuid.UUID, case: uuid.UUID, mailer: OutboxMailer) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await release_case_hold(conn, mailer, case)


async def test_a_job_of_a_case_on_hold_waits_and_runs_once_the_case_resumes(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    registry = isolated_registry()
    ran: list[uuid.UUID] = []

    @registry.kind("zz_work")
    async def work(ctx: JobContext) -> None:
        ran.append(ctx.job_id)

    hold(admin, team["case"])
    async with api_db.request(scope_for(team["expert"])) as conn:
        job = await registry.enqueue(conn, team["case"], "zz_work")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        await run_until_held(worker, admin, job)
        assert ran == [], "a job of a case on hold ran"
        assert job_row(admin, job) == ("queued", "case_on_hold")

        hold(admin, team["case"], False)
        await release(api_db, team["expert"], team["case"], OutboxMailer())
        assert await run_until_finished(worker, admin, [job]) == {job: "done"}
    assert ran == [job]
    assert job_row(admin, job) == ("done", None)


async def test_a_kind_that_deals_with_the_hold_itself_still_runs(
    api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    registry = isolated_registry()
    ran: list[uuid.UUID] = []

    @registry.kind("zz_hold_aware", runs_on_hold=True)
    async def work(ctx: JobContext) -> None:
        ran.append(ctx.job_id)

    hold(admin, team["case"])
    async with api_db.request(scope_for(team["expert"])) as conn:
        job = await registry.enqueue(conn, team["case"], "zz_hold_aware")
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await run_until_finished(worker, admin, [job]) == {job: "done"}
    assert ran == [job]


def listed(admin: psycopg.Connection, person: uuid.UUID) -> list[uuid.UUID]:
    """What the person's message list shows (the same filter as `GET /v1/notifications`)."""
    rows = admin.execute(
        "select id from app.notifications where person_id = %s and in_app and held_at is null", (person,)
    ).fetchall()
    return [r[0] for r in rows]


def daily_email(admin: psycopg.Connection, person: uuid.UUID, org: uuid.UUID) -> None:
    admin.execute(
        "insert into app.notification_preferences (person_id, organisation_id, channel, enabled, digest)"
        " values (%s, %s, 'email', true, true)",
        (person, org),
    )


async def test_messages_about_a_case_on_hold_wait_until_it_resumes(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case, org = team["case"], team["org"]
    daily_email(admin, team["viewer"], org)
    hold(admin, case)
    mailer = OutboxMailer()
    async with api_db.request(scope_for(team["expert"])) as conn:
        for person in (team["assistant"], team["viewer"]):
            assert await notify(conn, mailer, person_id=person, organisation_id=org, kind="zz.ping", case_id=case)
    assert mailer.outbox == []
    assert listed(admin, team["assistant"]) == [] and listed(admin, team["viewer"]) == []
    async with worker_db.anonymous() as conn:
        await send_daily(conn, date.today(), mailer)
    assert mailer.outbox == [], "a held message went in the daily email"

    hold(admin, case, False)
    await release(api_db, team["assistant"], case, mailer)
    assert [m.params for m in mailer.outbox] == [{"kind": "zz.ping"}]
    assert len(listed(admin, team["assistant"])) == 1 and len(listed(admin, team["viewer"])) == 1
    async with worker_db.anonymous() as conn:
        assert await send_daily(conn, date.today(), mailer) == 1, "the daily-email person gets it in the daily email"


async def test_a_message_about_the_hold_itself_is_sent_while_the_case_is_on_hold(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    hold(admin, team["case"])
    mailer = OutboxMailer()
    async with api_db.request(scope_for(team["expert"])) as conn:
        await notify(
            conn, mailer, person_id=team["assistant"], organisation_id=team["org"], kind="zz.held",
            case_id=team["case"], while_on_hold=True,
        )  # fmt: skip
    assert len(mailer.outbox) == 1
    assert len(listed(admin, team["assistant"])) == 1


async def test_messages_without_a_case_are_never_held(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    hold(admin, team["case"])
    mailer = OutboxMailer()
    async with api_db.request(scope_for(team["expert"])) as conn:
        await notify(conn, mailer, person_id=team["assistant"], organisation_id=team["org"], kind="zz.org")
    assert len(mailer.outbox) == 1


async def test_only_someone_who_may_resume_the_case_releases_it_and_only_once_it_is_off_hold(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    hold(admin, team["case"])
    with pytest.raises(psycopg.Error) as still_held:
        await release(api_db, team["expert"], team["case"], OutboxMailer())
    assert still_held.value.sqlstate == "AV409"
    hold(admin, team["case"], False)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await release(api_db, team["viewer"], team["case"], OutboxMailer())
