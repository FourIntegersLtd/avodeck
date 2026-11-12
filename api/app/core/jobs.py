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
- A worker that dies stops its heartbeat; `JobWorker.requeue_stalled()` puts its jobs back (F6), counted in
  `jobs.stall_requeues` (migration 9002): after `JOB_MAX_STALL_REQUEUES` (default 5) the next stall fails the job
  with `stalled_too_often` and dead-letters it instead.
- A job left `running` after its database connection dropped while the worker recorded how it ended is
  recorded again on a fresh connection (`DEFAULT_PERSIST_WAITS`); if that fails too, `requeue_orphaned()`
  (at worker start and every housekeeping round, migration 0134) puts it back within `max_attempts` (F52).
- Avodeck support resumes a paused job or requeues a failed one with `support_job_action()` (current
  staff only, in the database): a resumed job goes back on the queue and the worker moves it
  paused -> running; a requeued one goes failed -> queued with `attempts` 0, and what it was is kept in
  `job_support_actions`.
- Page-batch jobs (ADR 0038) lean on small hooks: a handler raises `JobParked` to leave its job `running` with
  no queue row (something else wakes it); a queue row for a job already `done` is ended without running;
  `registry.sweep(name)` runs a handler `(conn)` every housekeeping round; `ctx.limit_checkouts(cap)` caps the
  pool connections one job holds at once (`Database.job()` waits for a permit), ending with the handler call;
  `ctx.progress(..., monotone=True, job_id=...)` never lowers `pct` and may report on another job of the case.
  A pause with `busy_processing` is deliberately not in `RESUMABLE_PAUSES`: the job ends `paused` with no queue row.
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
from procrastinate.jobs import Job, Status
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from psycopg import AsyncConnection

from app.core.ai.kit import MAX_WAIT_SECONDS, backoff_delays
from app.core.config import DEFAULT_JOB_MAX_STALL_REQUEUES, DEFAULT_QUEUE_RETENTION_DAYS, JobTimeLimits
from app.core.db import Database, job_checkouts
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
SweepHandler = Callable[[AsyncConnection], Awaitable[None]]
DatabaseSweepHandler = Callable[["Database"], Awaitable[None]]

# F5: pauses the worker resumes by itself, because the service comes back without anyone acting.
RESUMABLE_PAUSES = frozenset({"service_busy", "model_unavailable"})
# BG-06: the pause codes under which a queue row may start a `paused` job. The two above resume themselves; `resuming`
# is what the shutdown pause and every person-driven or support resume set (`app.resume_paused_job`,
# `app.support_job_action`); `busy_processing` is the page-batch waiter the pipeline sweep hands back (it checks
# again, and pauses again if the run is still busy). A job paused for anything else (`cost_guard`, `cancelled`)
# waits for a person; a stray queue row does not restart it.
QUEUE_ROW_MAY_RESUME = RESUMABLE_PAUSES | {"resuming", "busy_processing"}
# BG-12: the code of a job that failed for good because a run went past its time limit.
TIMED_OUT = "timed_out"
# BG-14: finished queue rows are pruned at most this often (seconds), and this many rows in one statement.
PRUNE_EVERY_SECONDS = 24 * 60 * 60
PRUNE_BATCH = 5000
# Waits between resumes: doubling from the AI kit's base, F5's 2 hours in all; then the job fails.
# Waits (seconds) between tries when a status write loses its database connection (the pooler closed it).
# Seconds a stopping worker lets running jobs finish before it pauses them (they carry on at restart).
DEFAULT_SHUTDOWN_GRACE_SECONDS = 25
# The code of a job failed because its worker stalled more often than `max_stall_requeues` allows (F6).
STALLED_TOO_OFTEN = "stalled_too_often"
DEFAULT_PERSIST_WAITS: tuple[float, ...] = (0.25, 1.0, 3.0)
# Pool connections kept back from capped jobs for the worker's own status writes and sweeps (ADR 0038).
POOL_RESERVE = 2


class JobParked(Exception):  # noqa: N818 - a signal to the worker, not an error
    """Raised by a handler to park its job: it stays `running`, its queue row ends, nothing is recorded. Something
    else (a child's last write, the heal sweep) later defers a new queue row for the same job (ADR 0038)."""


def checkout_cap(*, pool_max: int, concurrency: int) -> int:
    """How many pool connections one capped job may hold: the pool less its reserve, shared by the jobs the worker
    runs at once, never below one."""
    return max(1, (pool_max - POOL_RESERVE) // max(1, concurrency))


DEFAULT_PAUSE_DELAYS: tuple[float, ...] = tuple(backoff_delays(64, limit=MAX_WAIT_SECONDS))


@dataclass(frozen=True)
class JobKind:
    name: str
    handler: JobHandler
    interactive: bool
    changes_case: bool
    runs_on_hold: bool = False
    time_limit_seconds: float | None = None  # BG-12: this kind's own limit; None takes the worker's per-queue one


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
        self.sweeps: dict[str, SweepHandler] = {}
        self.database_sweeps: dict[str, DatabaseSweepHandler] = {}

    def kind(
        self,
        name: str,
        *,
        interactive: bool = False,
        changes_case: bool = True,
        runs_on_hold: bool = False,
        time_limit_seconds: float | None = None,
    ) -> Callable[[JobHandler], JobHandler]:
        """`runs_on_hold`: the kind deals with a case on hold itself; other kinds wait for the case to resume.
        `time_limit_seconds`: the longest one run of this kind may take (default: the worker's limit for its queue)."""

        def register(handler: JobHandler) -> JobHandler:
            if name in self.kinds:
                raise ValueError(f"job kind {name!r} is registered twice")
            self.kinds[name] = JobKind(name, handler, interactive, changes_case, runs_on_hold, time_limit_seconds)
            return handler

        return register

    def schedule(self, name: str) -> Callable[[ScheduleHandler], ScheduleHandler]:
        def register(handler: ScheduleHandler) -> ScheduleHandler:
            if name in self.schedules:
                raise ValueError(f"schedule {name!r} is registered twice")
            self.schedules[name] = handler
            return handler

        return register

    def sweep(self, name: str) -> Callable[[SweepHandler], SweepHandler]:
        """Register a handler `(conn)` that runs on an anonymous worker connection in every housekeeping round."""

        def register(handler: SweepHandler) -> SweepHandler:
            if name in self.sweeps:
                raise ValueError(f"sweep {name!r} is registered twice")
            self.sweeps[name] = handler
            return handler

        return register

    def database_sweep(self, name: str) -> Callable[[DatabaseSweepHandler], DatabaseSweepHandler]:
        """Register a handler `(database)` for work that must not sit in one transaction (a slow call to another
        service, row by row). It runs in every housekeeping round after all the `sweep`s, so it cannot delay them."""

        def register(handler: DatabaseSweepHandler) -> DatabaseSweepHandler:
            if name in self.database_sweeps or name in self.sweeps:
                raise ValueError(f"sweep {name!r} is registered twice")
            self.database_sweeps[name] = handler
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

    def limit_checkouts(self, cap: int) -> None:
        """From now on this job's tasks hold at most `cap` pool connections at once (`Database.job()` waits for a
        permit). Ends with the handler call; the worker's own checkouts are never capped."""
        if cap < 1:
            raise ValueError("a checkout cap is at least one")
        job_checkouts.set(asyncio.Semaphore(cap))

    async def progress(
        self,
        pct: int,
        *,
        eta_seconds: int | None = None,
        step: str | None = None,
        step_done: int | None = None,
        step_total: int | None = None,
        monotone: bool = False,
        job_id: uuid.UUID | None = None,
    ) -> None:
        """Store progress. Counts ("40 of 900") belong to the step: a call that leaves them out keeps
        them, and a call that names a different step starts them again. `monotone`: `pct` never goes down.
        `job_id`: report on another job of this case (a batch child on its parent)."""
        async with self.transaction() as conn:
            await conn.execute(
                "update app.jobs set pct = case when %s then greatest(coalesce(pct, 0), %s::int) else %s::int end,"
                " eta_seconds = %s, step = coalesce(%s, step),"
                " step_done = case when %s::int is not null then %s::int"
                "                  when %s::text is not null and %s::text is distinct from step then null"
                "                  else step_done end,"
                " step_total = case when %s::int is not null then %s::int"
                "                   when %s::text is not null and %s::text is distinct from step then null"
                "                   else step_total end"
                " where id = %s",
                (
                    monotone,
                    pct,
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
                    job_id or self.job_id,
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


def job_time_limit(kind: JobKind, limits: JobTimeLimits) -> float:
    """Seconds one run of `kind` may take (BG-12): its own limit, else the one for its queue kind."""
    if kind.time_limit_seconds is not None:
        return kind.time_limit_seconds
    return limits.interactive if kind.interactive else limits.bulk


class _TimedOut(Exception):  # noqa: N818 - a signal inside the worker: a run went past its time limit (BG-12)
    """Raised in place of the handler's own error when its run was stopped by the time limit."""


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
        max_stall_requeues: int = DEFAULT_JOB_MAX_STALL_REQUEUES,
        time_limits: JobTimeLimits | None = None,
        queue_retention_days: int = DEFAULT_QUEUE_RETENTION_DAYS,
        prune_every_seconds: float = PRUNE_EVERY_SECONDS,
    ) -> None:
        """`queues`: the queues this worker takes jobs from; none means all of the registry's, the old one too.
        `max_stall_requeues`: how many times a job's stalls are put back before the next one fails it.
        `time_limits`: the longest one run of a job may take, per queue kind (BG-12; a kind may set its own).
        `queue_retention_days`: finished queue rows older than this are deleted, at most every `prune_every_seconds`
        (BG-14)."""
        self.registry = registry
        self.queues = list(registry.all_queues if queues is None else queues)
        self.mailer: Mailer = mailer or OutboxMailer()
        self.database = database
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.stalled_after_seconds = stalled_after_seconds
        self.max_attempts = int(max_attempts)
        self.max_stall_requeues = max_stall_requeues
        self.time_limits = time_limits if time_limits is not None else JobTimeLimits()
        self.queue_retention_days = queue_retention_days
        self.prune_every_seconds = prune_every_seconds
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
        last_prune = float("-inf")
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self.requeue_stalled()
                await self.requeue_orphaned()
                await self.run_sweeps()
                if loop.time() - last_prune >= self.prune_every_seconds:
                    last_prune = loop.time()
                    await self.prune_finished_queue_rows()
                if loop.time() - last_catch_up >= self.schedule_seconds:
                    last_catch_up = loop.time()
                    for name in self.registry.schedules:
                        await self.catch_up(name, datetime.now(UTC).date())
            except Exception as exc:  # noqa: BLE001 - keep working; the type is enough to investigate
                logger.error("worker housekeeping failed: %s", type(exc).__name__)
            await asyncio.sleep(max(self.stalled_after_seconds / 2, self.poll_seconds))

    async def run_sweeps(self) -> int:
        """Run every registered sweep once, each on its own anonymous connection, then the database sweeps (they take
        as long as they take, so they go last). One that fails logs only its name and the exception type (rule 6) and
        does not stop the others. Returns how many ran without failing."""
        ok = 0
        for name, handler in self.registry.sweeps.items():
            try:
                async with self.database.anonymous() as conn:
                    await handler(conn)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - the type only: messages may carry patient text
                logger.error("worker sweep %s failed: %s", name, type(exc).__name__)
        for name, database_handler in self.registry.database_sweeps.items():
            try:
                await database_handler(self.database)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - the type only: messages may carry patient text
                logger.error("worker sweep %s failed: %s", name, type(exc).__name__)
        return ok

    async def prune_finished_queue_rows(self) -> int:
        """Delete finished queue rows, and their events, older than `queue_retention_days`, and those of a deleted case
        (BG-14, audit 12 D1f): `app.prune_queue_rows` in batches. Ids and timestamps only were ever in them. Returns
        how many rows went."""
        total = 0
        for _ in range(100):  # a bound, so one round cannot run for ever
            async with self.database.anonymous() as conn:
                cur = await conn.execute(
                    "select app.prune_queue_rows(%s, %s)", (self.queue_retention_days, PRUNE_BATCH)
                )
                row = await cur.fetchone()
            removed = int(row[0]) if row else 0
            total += removed
            if removed < PRUNE_BATCH:
                break
        if total:
            logger.info("%d finished queue row(s) pruned", total)
        return total

    async def requeue_stalled(self) -> int:
        """Put back the jobs of workers whose heartbeat stopped (F6). Returns how many were put back.

        A job that has already been put back `max_stall_requeues` times is not put back again: it ends `failed`
        (`stalled_too_often`) through the dead-letter path, and its queue row ends with it."""
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
                    cur = await conn.execute(
                        "update app.jobs set requeued_stall = true, stall_requeues = stall_requeues + 1"
                        " where id = %s and status = 'running' returning kind, stall_requeues",
                        (uuid.UUID(job_id),),
                    )
                    noted = await cur.fetchone()
                if noted is not None and noted[1] > self.max_stall_requeues:
                    logger.warning("job %s of kind %s stalled %d times: failed", job_id, noted[0], noted[1])
                    await self._finish(uuid.UUID(case_id), uuid.UUID(job_id), "failed", error_code=STALLED_TOO_OFTEN)
                    await self.queue.job_manager.finish_job(job, Status.FAILED, delete_job=False)
                    _dead_letter(noted[0], uuid.UUID(job_id))
                    continue
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
            cur = await conn.execute("select kind, status from app.jobs where id = %s", (job,))
            known = await cur.fetchone()
            if known is not None and known[1] == "done":
                return  # a second queue row for a job that already finished (a parent woken twice): end it, F60
            found = self.registry.kinds.get(known[0]) if known else None
            runs_on_hold = found is not None and found.runs_on_hold
            cur = await conn.execute("select app.job_waits_for_hold(%s, %s)", (job, runs_on_hold))
            waits = await cur.fetchone()
            if waits is not None and waits[0]:
                return  # stays queued; `release_case_hold()` puts it back when the case resumes
            cur = await conn.execute(
                # Resuming from a pause, or after a stalled worker, is not a new attempt: `attempts` counts
                # failures only. The claim names the states a queue row may start (BG-05, BG-06): a `queued` job; a
                # `running` one only when the worker's own stall requeue or a parked parent's wake marked it
                # (`requeued_stall`); a `paused` one only for a code the worker may resume. A second queue row for a
                # job that is running, failed or waiting for a person claims nothing and ends.
                "update app.jobs set status = 'running', error_code = null, requeued_stall = false,"
                " attempts = attempts + case when status = 'paused' or requeued_stall then 0 else 1 end,"
                " started_at = coalesce(started_at, now()) where id = %s"
                " and (status = 'queued' or (status = 'running' and requeued_stall)"
                " or (status = 'paused' and error_code = any(%s)))"
                " returning kind, attempts",
                (job, sorted(QUEUE_ROW_MAY_RESUME)),
            )
            row = await cur.fetchone()
        if row is None:
            if known is not None:  # the job exists but this row may not start it: a duplicate or a stray row
                logger.warning("queue row for job %s ignored: the job is %s", job, known[1])
            return  # nothing to do: the job's row is gone (case deleted), or this queue row has no right to start it
        kind_name, attempt = row
        kind = self.registry.kinds.get(kind_name)
        if kind is None:
            await self._finish(case, job, "failed", error_code="unknown_kind")
            _dead_letter(kind_name, job)
            return
        limit = asyncio.timeout(job_time_limit(kind, self.time_limits))
        try:
            try:
                async with limit:
                    await kind.handler(JobContext(self.database, job, case, kind_name, attempt, mailer=self.mailer))
            except TimeoutError:
                if not limit.expired():
                    raise  # the handler's own timeout, an ordinary failure
                raise _TimedOut from None
            finally:
                # The checkout limit ends with the handler call, before the status below is written: a handler that
                # raises while holding all its permits must not leave the worker waiting for one.
                job_checkouts.set(None)
        except JobParked:
            return  # stays `running`; the queue row ends; whoever parked it wakes it
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
            await self._finish(case, job, "failed", error_code=TIMED_OUT if isinstance(exc, _TimedOut) else "job_error")
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
