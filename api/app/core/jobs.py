"""The job queue: Procrastinate on Postgres, with Avodeck's `jobs` row as the record people see (architecture 2, 7).

- `registry.kind(name)` registers a job kind. Each slice registers its own kinds; the worker runs them.
- `registry.enqueue(conn, case_id, kind)` queues a job inside the caller's transaction. The database
  checks case access first (`app.enqueue_job()`), writes the `jobs` row and hands the job to the queue.
- Jobs that change a case share the lock `case:<id>`, so only one runs per case at a time; jobs that
  only read (`changes_case=False`) and other cases' jobs run alongside. Interactive jobs run first.
- Each job runs as `app_worker` for its own case only (`Database.job()`, ADR 0013). Progress goes on
  the `jobs` row as numbers and short codes, never text (rule 6). `ctx.step()` records finished steps
  so a retried or requeued job skips them (F6).
- A job that raises is retried up to `max_attempts`, then ends `failed` with `job_error`; one that
  raises `ProcessingPaused` ends `paused` with its reason. A pause for a busy or unreachable service
  (`RESUMABLE_PAUSES`, F5) goes back on the queue after an exponential back-off (`DEFAULT_PAUSE_DELAYS`,
  2 hours in all) and carries on when the service answers; still paused after that, it ends `failed`
  with the pause reason so a person can retry it. Other pauses (cost guard K3, cancelled) wait for a person.
- `registry.schedule(name)` registers a daily job keyed by date; `JobWorker.catch_up()` runs every
  date since the last run exactly once (F28).
- A job of a case on hold is not run: it stays `queued` with `case_on_hold` until the case resumes
  (`app.core.case_hold.release_case_hold`), unless its kind is registered with `runs_on_hold=True`.
- A worker that dies stops its heartbeat; `JobWorker.requeue_stalled()` puts its jobs back (F6).
- A job left `running` after its database connection dropped while the worker recorded how it ended is
  recorded again on a fresh connection (`DEFAULT_PERSIST_WAITS`); if that fails too, `requeue_orphaned()`
  (at worker start and every housekeeping round, migration 0134) puts it back within `max_attempts` (F52).
- Avodeck support resumes a paused job or requeues a failed one with `support_job_action()` (current
  staff only, in the database): a resumed job goes back on the queue and the worker moves it
  paused -> running; a requeued one goes failed -> queued with `attempts` 0, and what it was is kept in
  `job_support_actions`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import procrastinate
import psycopg
import sentry_sdk
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from psycopg import AsyncConnection

from app.core.ai.kit import MAX_WAIT_SECONDS, backoff_delays
from app.core.db import Database
from app.core.email import Mailer, OutboxMailer
from app.core.errors import ProcessingPaused

logger = logging.getLogger("app.jobs")

INTERACTIVE_PRIORITY = 10
BATCH_PRIORITY = 0
TASK_NAME = "run_job"
# Queues (scalability PR 7): work a person waits for runs apart from hours of bundle processing. `jobs` is the
# one queue the app used before the split; it is still read, so work queued before a rollout is picked up.
INTERACTIVE_QUEUE = "interactive"
BULK_QUEUE = "bulk"
LEGACY_QUEUE = "jobs"

JobHandler = Callable[["JobContext"], Awaitable[None]]
ScheduleHandler = Callable[[AsyncConnection, date], Awaitable[None]]
StepWork = Callable[[AsyncConnection], Awaitable[None]]

# F5: pauses the worker resumes by itself, because the service comes back without anyone acting.
RESUMABLE_PAUSES = frozenset({"service_busy", "model_unavailable"})
# Waits between resumes: doubling from the AI kit's base, F5's 2 hours in all; then the job fails.
# Waits (seconds) between tries when a status write loses its database connection (the pooler closed it).
# Seconds a stopping worker lets running jobs finish before it pauses them (they carry on at restart).
DEFAULT_SHUTDOWN_GRACE_SECONDS = 25
DEFAULT_PERSIST_WAITS: tuple[float, ...] = (0.25, 1.0, 3.0)
DEFAULT_PAUSE_DELAYS: tuple[float, ...] = tuple(backoff_delays(64, limit=MAX_WAIT_SECONDS))


@dataclass(frozen=True)
class JobKind:
    name: str
    handler: JobHandler
    interactive: bool
    changes_case: bool
    runs_on_hold: bool = False


class JobRegistry:
    """Job kinds and daily schedules, by name. The app's own is `registry`; tests make their own."""

    def __init__(self, queue: str = LEGACY_QUEUE, *, split_queues: bool = False, queue_prefix: str = "") -> None:
        """`queue` is the one queue everything runs on, or, with `split_queues`, only the old one still read.
        With `split_queues` an interactive kind runs on `interactive`, every other kind on `bulk`
        (`queue_prefix` keeps a test's queues apart)."""
        self.queue = queue
        self.split_queues = split_queues
        self.queue_prefix = queue_prefix
        self.kinds: dict[str, JobKind] = {}
        self.schedules: dict[str, ScheduleHandler] = {}

    def kind(
        self, name: str, *, interactive: bool = False, changes_case: bool = True, runs_on_hold: bool = False
    ) -> Callable[[JobHandler], JobHandler]:
        """`runs_on_hold`: the kind deals with a case on hold itself; other kinds wait for the case to resume."""

        def register(handler: JobHandler) -> JobHandler:
            if name in self.kinds:
                raise ValueError(f"job kind {name!r} is registered twice")
            self.kinds[name] = JobKind(name, handler, interactive, changes_case, runs_on_hold)
            return handler

        return register

    def schedule(self, name: str) -> Callable[[ScheduleHandler], ScheduleHandler]:
        def register(handler: ScheduleHandler) -> ScheduleHandler:
            if name in self.schedules:
                raise ValueError(f"schedule {name!r} is registered twice")
            self.schedules[name] = handler
            return handler

        return register

    @property
    def interactive_queue(self) -> str:
        return f"{self.queue_prefix}{INTERACTIVE_QUEUE}"

    @property
    def bulk_queue(self) -> str:
        return f"{self.queue_prefix}{BULK_QUEUE}"

    @property
    def all_queues(self) -> list[str]:
        """Every queue a worker with no queue list takes, the old one included."""
        return [self.interactive_queue, self.bulk_queue, self.queue] if self.split_queues else [self.queue]

    def queue_for(self, kind: str) -> str:
        """The queue a kind is queued on. A kind this registry does not know stays on the old queue."""
        found = self.kinds.get(kind)
        if not self.split_queues or found is None:
            return self.queue
        return self.interactive_queue if found.interactive else self.bulk_queue

    async def enqueue(self, conn: AsyncConnection, case_id: uuid.UUID, kind: str) -> uuid.UUID:
        """Queue a job on the case. The database decides the case action the kind needs
        (`app.job_kind_actions`; `case.upload` if not listed) and checks the person has it."""
        found = self.kinds[kind]
        cur = await conn.execute(
            "select app.enqueue_job(%s, %s, %s, %s, %s, %s)",
            (
                case_id,
                kind,
                self.queue_for(kind),
                INTERACTIVE_PRIORITY if found.interactive else BATCH_PRIORITY,
                found.changes_case,
                os.environ.get("CODE_VERSION") or None,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]  # type: ignore[no-any-return]


registry = JobRegistry(split_queues=True)

SUPPORT_JOB_ACTIONS = ("resume", "requeue")


@dataclass(frozen=True)
class SupportJobOutcome:
    """`outcome` is `ok`, `not_found`, `job_not_paused`, `job_not_failed` or `case_on_hold`; nothing was
    written unless it is `ok`. `expert_email` is the named expert's, for the patient-free notice."""

    outcome: str
    case_id: uuid.UUID | None
    status: str | None
    expert_email: str | None


async def support_job_action(
    conn: AsyncConnection, job_id: uuid.UUID, action: str, reason_code: str, *, queue: str | None = None
) -> SupportJobOutcome:
    """Support resumes a paused job (`resume`) or requeues a failed one (`requeue`)
    (`app.support_job_action()`, migration 0035). Raises 42501 for anyone not current support staff.
    A job still waiting in the queue is brought forward on the queue it is on. A job that needs a new queue row goes
    on `queue`, else on the queue its kind runs on (an interactive kind such as export_case on `interactive`)."""
    if action not in SUPPORT_JOB_ACTIONS:
        raise ValueError(f"unknown support job action {action!r}")
    if queue is None:
        cur = await conn.execute("select app.support_job_kind(%s)", (job_id,))
        found = await cur.fetchone()
        queue = registry.queue_for(found[0]) if found and found[0] else registry.queue
    cur = await conn.execute(
        "select outcome, case_id, status, expert_email from app.support_job_action(%s, %s, %s, %s)",
        (job_id, action, reason_code, queue),
    )
    row = await cur.fetchone()
    assert row is not None
    return SupportJobOutcome(*row)


class JobContext:
    """What a running job gets: who it is, its own case's transactions, progress and steps."""

    def __init__(
        self,
        database: Database,
        job_id: uuid.UUID,
        case_id: uuid.UUID,
        kind: str,
        attempt: int,
        *,
        mailer: Mailer | None = None,
    ) -> None:
        self.database = database
        self.mailer: Mailer = mailer or OutboxMailer()
        self.job_id = job_id
        self.case_id = case_id
        self.kind = kind
        self.attempt = attempt

    def transaction(self) -> contextlib.AbstractAsyncContextManager[AsyncConnection]:
        """A transaction as `app_worker` that sees this job's case only."""
        return self.database.job(self.case_id)

    async def progress(
        self,
        pct: int,
        *,
        eta_seconds: int | None = None,
        step: str | None = None,
        step_done: int | None = None,
        step_total: int | None = None,
    ) -> None:
        """Store progress. Counts ("40 of 900") belong to the step: a call that leaves them out keeps
        them, and a call that names a different step starts them again."""
        async with self.transaction() as conn:
            await conn.execute(
                "update app.jobs set pct = %s, eta_seconds = %s, step = coalesce(%s, step),"
                " step_done = case when %s::int is not null then %s::int"
                "                  when %s::text is not null and %s::text is distinct from step then null"
                "                  else step_done end,"
                " step_total = case when %s::int is not null then %s::int"
                "                   when %s::text is not null and %s::text is distinct from step then null"
                "                   else step_total end"
                " where id = %s",
                (
                    pct,
                    eta_seconds,
                    step,
                    step_done,
                    step_done,
                    step,
                    step,
                    step_total,
                    step_total,
                    step,
                    step,
                    self.job_id,
                ),
            )

    async def step(self, key: str, work: StepWork) -> bool:
        """Run `work` once per job: skipped if this step already finished. Returns whether it ran."""
        async with self.transaction() as conn:
            cur = await conn.execute("select 1 from app.job_steps where job_id = %s and step = %s", (self.job_id, key))
            if await cur.fetchone() is not None:
                return False
            await work(conn)
            await conn.execute(
                "insert into app.job_steps (job_id, case_id, step) values (%s, %s, %s)",
                (self.job_id, self.case_id, key),
            )
        return True


class _TryAgain(Exception):
    """Raised to the queue when a failed job has attempts left."""


class _ResumeLater(Exception):
    """Raised to the queue when a paused job should run again after `wait_seconds` (F5)."""

    def __init__(self, wait_seconds: float) -> None:
        super().__init__()
        self.wait_seconds = wait_seconds


class _RetryOnlyWhenAsked(BaseRetryStrategy):
    def __init__(self, wait_seconds: float) -> None:
        self.wait_seconds = wait_seconds

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if isinstance(exception, _TryAgain):
            return RetryDecision(retry_at=datetime.now(UTC) + timedelta(seconds=self.wait_seconds))
        if isinstance(exception, _ResumeLater):
            # The same queue row goes back to `todo`, so the job can never run twice at once.
            return RetryDecision(retry_at=datetime.now(UTC) + timedelta(seconds=exception.wait_seconds))
        return None


def _dead_letter(kind: str, job: uuid.UUID) -> None:
    """A job ended `failed` for good: one Sentry event with its kind and id, nothing else (rule 6). Sentry's
    alert rule on `job_dead_lettered` pages the operators."""
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("kind", kind)
        scope.set_tag("job_id", str(job))
        sentry_sdk.capture_message("job_dead_lettered", level="error")


class JobWorker:
    """Runs a registry's jobs from its queue. Use as `async with JobWorker(...) as worker`."""

    def __init__(
        self,
        registry: JobRegistry,
        *,
        database: Database,
        dsn: str,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 10,
        stalled_after_seconds: float = 60,
        max_attempts: float = 3,
        retry_wait_seconds: float = 5,
        schedule_seconds: float = 3600,
        pause_delays: Sequence[float] = DEFAULT_PAUSE_DELAYS,
        mailer: Mailer | None = None,
        queue_pool_max: int = 4,
        persist_waits: Sequence[float] = DEFAULT_PERSIST_WAITS,
        orphan_grace_seconds: float | None = None,
        shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
        queues: Sequence[str] | None = None,
    ) -> None:
        """`queues`: the queues this worker takes jobs from; none means all of the registry's, the old one too."""
        self.registry = registry
        self.queues = list(registry.all_queues if queues is None else queues)
        self.mailer: Mailer = mailer or OutboxMailer()
        self.database = database
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.stalled_after_seconds = stalled_after_seconds
        self.max_attempts = int(max_attempts)
        self.schedule_seconds = schedule_seconds
        self.pause_delays = tuple(pause_delays)
        self.persist_waits = tuple(persist_waits)
        self.orphan_grace_seconds = stalled_after_seconds if orphan_grace_seconds is None else orphan_grace_seconds
        connector = procrastinate.PsycopgConnector(
            conninfo=dsn, kwargs={"options": "-c search_path=procrastinate"}, min_size=1, max_size=queue_pool_max
        )
        self.queue = procrastinate.App(connector=connector)
        self.queue.task(
            name=TASK_NAME, queue=registry.queue, retry=_RetryOnlyWhenAsked(retry_wait_seconds), pass_context=True
        )(self._run_job)

    async def __aenter__(self) -> JobWorker:
        await self.queue.open_async()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.queue.close_async()

    async def run(self, *, concurrency: int = 1, install_signal_handlers: bool = False) -> None:
        """Work until cancelled (or until a signal, with `install_signal_handlers`)."""
        housekeeping = asyncio.create_task(self._housekeeping())
        try:
            await self.queue.run_worker_async(
                queues=self.queues,
                concurrency=concurrency,
                wait=True,
                fetch_job_polling_interval=self.poll_seconds,
                listen_notify=False,
                install_signal_handlers=install_signal_handlers,
                update_heartbeat_interval=self.heartbeat_seconds,
                stalled_worker_timeout=self.stalled_after_seconds,
                shutdown_graceful_timeout=self.shutdown_grace_seconds,
            )
        finally:
            housekeeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await housekeeping

    async def _housekeeping(self) -> None:
        last_catch_up = float("-inf")
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self.requeue_stalled()
                await self.requeue_orphaned()
                if loop.time() - last_catch_up >= self.schedule_seconds:
                    last_catch_up = loop.time()
                    for name in self.registry.schedules:
                        await self.catch_up(name, datetime.now(UTC).date())
            except Exception as exc:  # noqa: BLE001 - keep working; the type is enough to investigate
                logger.error("worker housekeeping failed: %s", type(exc).__name__)
            await asyncio.sleep(max(self.stalled_after_seconds / 2, self.poll_seconds))

    async def requeue_stalled(self) -> int:
        """Put back the jobs of workers whose heartbeat stopped (F6). Returns how many."""
        stalled: list[Job] = []
        for name in self.queues:
            stalled += await self.queue.job_manager.get_stalled_jobs(
                queue=name, seconds_since_heartbeat=self.stalled_after_seconds
            )
        count = 0
        for job in stalled:
            # A stall is not a failure: the next run does not count an attempt (migration 0058).
            job_id, case_id = job.task_kwargs.get("job_id"), job.task_kwargs.get("case_id")
            if isinstance(job_id, str) and isinstance(case_id, str):
                async with self.database.job(uuid.UUID(case_id)) as conn:
                    await conn.execute(
                        "update app.jobs set requeued_stall = true where id = %s and status = 'running'",
                        (uuid.UUID(job_id),),
                    )
            await self.queue.job_manager.retry_job(job)
            count += 1
        return count

    async def requeue_orphaned(self) -> int:
        """Put back jobs left `running` whose queue row has ended, e.g. after the database connection dropped
        while the worker recorded how the job ended (migration 0134). Returns how many."""
        async with self.database.anonymous() as conn:
            cur = await conn.execute(
                "select app.requeue_orphaned_jobs(%s, %s)", (self.max_attempts, self.orphan_grace_seconds)
            )
            row = await cur.fetchone()
        count = int(row[0]) if row else 0
        if count:
            logger.warning("%d job(s) left running without a queue row were put back", count)
        return count

    async def _persist(self, write: Callable[[], Awaitable[None]]) -> None:
        """Runs a status write, trying again on a fresh connection when the connection was dropped. The last
        try's error is raised; the sweep (`requeue_orphaned`) then puts the job right."""
        for wait in self.persist_waits:
            try:
                await write()
                return
            except psycopg.OperationalError as exc:
                logger.warning("recording a job's status lost its connection (%s): trying again", type(exc).__name__)
                await asyncio.sleep(wait)
        await write()

    async def catch_up(self, name: str, today: date) -> list[date]:
        """Run schedule `name` for every date since its last run, up to `today`, each exactly once (F28).

        A date that fails is not recorded, so the next catch-up tries it again; the error is raised.
        """
        handler = self.registry.schedules[name]
        async with self.database.anonymous() as conn:
            cur = await conn.execute("select app.last_scheduled_run(%s)", (name,))
            row = await cur.fetchone()
        last: date | None = row[0] if row else None
        start = today if last is None else last + timedelta(days=1)
        ran: list[date] = []
        day = start
        while day <= today:
            async with self.database.anonymous() as conn:
                cur = await conn.execute("select app.claim_scheduled_run(%s, %s)", (name, day))
                claimed = await cur.fetchone()
                if claimed is not None and claimed[0]:
                    await handler(conn, day)
                    ran.append(day)
            day += timedelta(days=1)
        return ran

    async def _run_job(self, queued: procrastinate.JobContext, job_id: str, case_id: str) -> None:
        job, case = uuid.UUID(job_id), uuid.UUID(case_id)
        async with self.database.job(case) as conn:
            cur = await conn.execute("select kind from app.jobs where id = %s", (job,))
            known = await cur.fetchone()
            found = self.registry.kinds.get(known[0]) if known else None
            runs_on_hold = found is not None and found.runs_on_hold
            cur = await conn.execute("select app.job_waits_for_hold(%s, %s)", (job, runs_on_hold))
            waits = await cur.fetchone()
            if waits is not None and waits[0]:
                return  # stays queued; `release_case_hold()` puts it back when the case resumes
            cur = await conn.execute(
                # Resuming from a pause, or after a stalled worker, is not a new attempt: `attempts` counts
                # failures only.
                "update app.jobs set status = 'running', error_code = null, requeued_stall = false,"
                " attempts = attempts + case when status = 'paused' or requeued_stall then 0 else 1 end,"
                " started_at = coalesce(started_at, now()) where id = %s returning kind, attempts",
                (job,),
            )
            row = await cur.fetchone()
        if row is None:
            return  # the job's row is gone (case deleted): nothing to do
        kind_name, attempt = row
        kind = self.registry.kinds.get(kind_name)
        if kind is None:
            await self._finish(case, job, "failed", error_code="unknown_kind")
            _dead_letter(kind_name, job)
            return
        try:
            await kind.handler(JobContext(self.database, job, case, kind_name, attempt, mailer=self.mailer))
        except asyncio.CancelledError:
            # The worker is stopping and its grace has run out: pause the job (no attempt counted) and put its
            # queue row back, so it carries on where it stopped when a worker starts again.
            await self._finish(case, job, "paused", error_code="resuming")
            raise _ResumeLater(0) from None
        except ProcessingPaused as paused:
            if paused.reason not in RESUMABLE_PAUSES:
                await self._finish(case, job, "paused", error_code=paused.reason)
                return
            # Runs of this queue row so far, less the failed attempts and requeues: the resumes after a pause.
            resumes = max(0, queued.job.attempts - (attempt - 1))
            if resumes < len(self.pause_delays):
                await self._finish(case, job, "paused", error_code=paused.reason)
                raise _ResumeLater(self.pause_delays[resumes]) from None
            logger.warning("job %s of kind %s still paused (%s) after back-off: failed", job, kind_name, paused.reason)
            await self._finish(case, job, "failed", error_code=paused.reason)
            _dead_letter(kind_name, job)
            return
        except Exception as exc:  # noqa: BLE001 - the type only: messages may carry patient text
            logger.warning("job %s of kind %s failed on attempt %d: %s", job, kind_name, attempt, type(exc).__name__)
            if attempt < self.max_attempts:
                # running -> failed -> queued in ONE transaction (the guard allows each step): under read
                # committed no one else sees `failed`, so a reader never takes a retry for the end. Each step
                # names the status it moves from, so a retry after a commit that went through changes nothing.
                async def requeue() -> None:
                    async with self.database.job(case) as conn:
                        await conn.execute(
                            "update app.jobs set status = 'failed', error_code = 'job_error'"
                            " where id = %s and status = 'running'",
                            (job,),
                        )
                        await conn.execute(
                            "update app.jobs set status = 'queued', finished_at = null"
                            " where id = %s and status = 'failed'",
                            (job,),
                        )

                await self._persist(requeue)
                raise _TryAgain from None
            await self._finish(case, job, "failed", error_code="job_error")
            _dead_letter(kind_name, job)
            return
        await self._finish(case, job, "done", pct=100)

    async def _finish(
        self, case: uuid.UUID, job: uuid.UUID, status: str, *, error_code: str | None = None, pct: int | None = None
    ) -> None:

        async def write() -> None:
            async with self.database.job(case) as conn:
                await conn.execute(
                    "update app.jobs set status = %s, error_code = %s, pct = coalesce(%s, pct),"
                    " eta_seconds = case when %s = 'done' then 0 else eta_seconds end,"
                    " finished_at = case when %s in ('done', 'failed') then now() end where id = %s",
                    (status, error_code, pct, status, status, job),
                )

        await self._persist(write)
