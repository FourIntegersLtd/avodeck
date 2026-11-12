"""Background jobs, audit 15, area B continued: queue rows that do not match their job (tampering) and replay.

The queue payload is only `{job_id, case_id}`. The worker re-reads the job row under the case key it was given, so a
row naming the wrong case must run nothing. These tests make queue rows by hand (the way an attacker with the worker
credential, or a bug, could) and run the real worker on them.

9b (a case on hold before the run) is covered by `tests/repository/test_foundation_case_hold_queue.py`.
9d (what `export_case` and the transcript exports re-check) is read-only: see the audit report.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext, JobRegistry
from tests.support.background_jobs import CANARY, defer, job_row, queue_rows, wait_for
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ENDED = ("succeeded", "failed", "cancelled", "aborted")


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


class World:
    def __init__(self, seed: Seed) -> None:
        self.expert = seed.person()
        self.case = seed.case(seed.org(kind="personal", members={self.expert: "owner"}), self.expert)
        other = seed.person()
        self.other_case = seed.case(seed.org(kind="personal", members={other: "owner"}), other)
        self.seed = seed
        self.ran: list[uuid.UUID] = []
        self.registry: JobRegistry = isolated_registry()
        self.sleep = 0.0
        self.pause_first_with: str | None = None

        @self.registry.kind("zz_work", changes_case=False)
        async def work(ctx: JobContext) -> None:
            self.ran.append(ctx.job_id)
            if self.pause_first_with and self.ran.count(ctx.job_id) == 1:
                raise ProcessingPaused(self.pause_first_with)
            await asyncio.sleep(self.sleep)

        @self.registry.kind("zz_locked", changes_case=True)
        async def locked(ctx: JobContext) -> None:
            self.ran.append(ctx.job_id)
            await asyncio.sleep(self.sleep)

    @property
    def queue(self) -> str:
        return self.registry.queue_for("zz_work")

    def job(self, kind: str = "zz_work", case: uuid.UUID | None = None) -> uuid.UUID:
        return self.seed.job(case or self.case, kind=kind, requested_by=self.expert)


@pytest.fixture
def world(seed: Seed) -> World:
    return World(seed)


async def drive(
    world: World,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    jobs: list[uuid.UUID],
    *,
    concurrency: int = 1,
) -> None:
    """Run the real worker until every queue row naming these jobs has ended (or twenty seconds pass)."""

    def settled() -> bool:
        rows = [s for j in jobs for _, s in queue_rows(admin, j)]
        return bool(rows) and all(s in ENDED for s in rows)

    async with open_worker(world.registry, worker_db, worker_dsn) as worker:
        task = asyncio.create_task(worker.run(concurrency=concurrency))
        try:
            with contextlib.suppress(AssertionError):
                await wait_for(settled, timeout=20)
            await asyncio.sleep(0.3)  # a row that should not run has had its chance
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


# --- 7. rows that do not match their job ---------------------------------------------------------------------------


async def test_a_queue_row_naming_another_cases_key_runs_nothing_and_changes_nothing(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    genuine = world.job()
    stray = world.job()  # a job of the expert's case, queued with the OTHER case's id in its payload
    defer(admin, world.queue, genuine, world.case)
    defer(admin, world.queue, stray, world.other_case)
    await drive(world, worker_db, worker_dsn, admin, [genuine, stray])
    assert world.ran == [genuine], "control: the matching row ran, the mismatched one did not"
    assert job_row(admin, genuine)[0] == "done"
    assert job_row(admin, stray) == ("queued", 0, None), job_row(admin, stray)
    assert all(s in ENDED for _, s in queue_rows(admin, stray)), "the mismatched row ended instead of looping"


async def test_a_queue_row_with_a_job_id_that_does_not_exist_ends_without_error(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    ghost = uuid.uuid4()
    defer(admin, world.queue, ghost, world.case)
    await drive(world, worker_db, worker_dsn, admin, [ghost])
    assert world.ran == []
    assert queue_rows(admin, ghost) == [(world.queue, "succeeded")]


async def test_a_job_of_an_unknown_kind_ends_failed_and_runs_nothing(
    world: World, api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    unknown = world.job("zz_not_registered")
    known = world.job()
    for job in (unknown, known):
        defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [unknown, known])
    assert world.ran == [known], "control: the registered kind ran"
    assert job_row(admin, unknown) == ("failed", 1, "unknown_kind")


# --- 8. replay -------------------------------------------------------------------------------------------------------


async def test_a_second_queue_row_for_a_finished_job_does_not_run_it_again(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    job = world.job()
    defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert world.ran == [job] and job_row(admin, job)[0] == "done", "control: it ran once"
    defer(admin, world.queue, job, world.case)  # the replay
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert world.ran == [job]
    assert [s for _, s in queue_rows(admin, job)] == ["succeeded", "succeeded"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BG-05: two queue rows for one queued job with no case lock run the handler twice (the worker only skips"
    " a job that is already `done`; it does not refuse one that is `running`)",
)
async def test_two_queue_rows_for_one_job_on_two_workers_run_the_handler_once(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    world.sleep = 0.8
    job = world.job()
    for _ in range(2):
        defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job], concurrency=2)
    assert world.ran == [job], f"the handler ran {len(world.ran)} times for one job"


async def test_control_two_queue_rows_for_a_case_locked_job_run_it_once(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    """The case lock (`case:<id>`, taken by every `changes_case` kind) makes the second row wait, and it then finds the
    job done. So the duplicate is only a problem for the kinds that do not change the case."""
    world.sleep = 0.5
    job = world.job("zz_locked")
    for _ in range(2):
        defer(admin, world.queue, job, world.case, lock=f"case:{world.case}")
    await drive(world, worker_db, worker_dsn, admin, [job], concurrency=2)
    assert world.ran == [job]


async def test_a_failed_job_with_a_new_queue_row_is_not_run_again(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    job = world.job()
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error', attempts = 3 where id = %s", (job,))
    defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert world.ran == []
    assert job_row(admin, job) == ("failed", 3, "job_error")
    # control: the legitimate way back (failed -> queued by the function the retry button calls) does run it
    admin.execute("update app.jobs set status = 'queued', attempts = 0 where id = %s", (job,))
    defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert world.ran == [job]


async def _pause_then_stray_row(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> uuid.UUID:
    """Run a job that pauses itself for a reason that waits for a person, then queue a stray second row for it and
    let the worker see that row. Returns the job id."""
    world.pause_first_with = "cost_guard"
    job = world.job()
    defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job])
    defer(admin, world.queue, job, world.case)  # the stray row
    await drive(world, worker_db, worker_dsn, admin, [job])
    return job


async def test_control_a_job_paused_for_a_person_is_recorded_paused_with_its_reason(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    world.pause_first_with = "cost_guard"
    job = world.job()
    defer(admin, world.queue, job, world.case)
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert job_row(admin, job)[0] == "paused" and job_row(admin, job)[2] == "cost_guard"
    assert world.ran == [job]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BG-06: a stray queue row resumes a job paused for a reason that waits for a person (cost_guard), because"
    " paused -> running is allowed and the worker does not look at the pause reason",
)
async def test_a_stray_queue_row_does_not_resume_a_job_paused_for_a_person(
    world: World, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    job = await _pause_then_stray_row(world, worker_db, worker_dsn, admin)
    assert world.ran == [job], "the handler ran again"


# --- 9. permission that changed between queueing and running --------------------------------------------------------


async def test_documented_a_job_still_runs_after_the_person_who_queued_it_loses_access(
    world: World, api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    """By design a job is the case's work, not the person's: the check is at enqueue time (`app.enqueue_job`) and the
    worker runs under the case key. What is produced FOR a person is checked when it is handed over (downloads check
    `requested_by` and the action again). This pins the behaviour; reading notes for the kinds that produce output
    for the requester are in the audit report (9d)."""
    assistant = world.seed.person()
    case = world.seed.case(
        world.seed.org(kind="team", members={world.expert: "owner"}), world.expert, members={assistant: "assistant"}
    )
    async with api_db.request(scope_for(assistant)) as conn:
        job = await world.registry.enqueue(conn, case, "zz_work")
    assert admin.execute("select requested_by from app.jobs where id = %s", (job,)).fetchone() == (assistant,)
    admin.execute("delete from app.case_members where case_id = %s and person_id = %s", (case, assistant))
    await drive(world, worker_db, worker_dsn, admin, [job])
    assert world.ran == [job]


async def test_a_job_whose_row_is_gone_when_its_queue_row_runs_is_skipped(
    world: World, api_db: Database, worker_db: Database, worker_dsn: str, admin: psycopg.Connection
) -> None:
    """The case was purged between queueing and running (the job row goes with the case): nothing runs, no error."""
    async with api_db.request(scope_for(world.expert)) as conn:
        gone = await world.registry.enqueue(conn, world.case, "zz_work")
        kept = await world.registry.enqueue(conn, world.case, "zz_work")
    admin.execute("delete from app.jobs where id = %s", (gone,))
    await drive(world, worker_db, worker_dsn, admin, [gone, kept])
    assert world.ran == [kept], "control: the other job ran"
    assert queue_rows(admin, gone) == [(world.queue, "succeeded")]
    assert CANARY not in str(job_row(admin, kept))
