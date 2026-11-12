"""Background jobs, audit 15, area A: what the worker and cron processes log, and whether they can raise an alert.

Each test asserts the SAFE outcome. A test the product fails is marked `xfail(strict=True, reason="BG-nn ...")`.

A1. The worker (`python -m app.worker.main`) and the cron service (`python -m app.worker.schedules`) are separate
    processes from the API. Only the API's `create_app` installs the scrubbing log record factory
    (`logs.configure`) and starts Sentry (`logs.init_sentry`). The entrypoints are run here through their real
    `main()` (database, queue and features are real; only the endless `JobWorker.run` is replaced by a no-op).
A2. With the logging a worker really has (no scrubbing factory), a handler, a sweep and a schedule that raise an
    error whose message is a canary: the canary must not reach any log record, traceback or record attribute.
A3. The same canary must not reach `app.jobs`, `app.job_steps`, `procrastinate.procrastinate_jobs` or
    `procrastinate.procrastinate_events`.

All data is synthetic. The canary stands in for patient text.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import psycopg
import pytest
import sentry_sdk

from app.core import logs
from app.core.db import Database
from app.core.jobs import JobContext, JobRegistry, JobWorker
from tests.support.background_jobs import (
    CANARY,
    capturing_all_logs,
    logging_as_the_worker_has_it,
    scrubbing_installed,
)
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

CANARY_LOWER = CANARY.lower().replace("-", "_")  # a legal `job_steps.step` value, for a real unique violation
FORBIDDEN = (CANARY, CANARY_LOWER)

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
def clean_process_logging() -> Iterator[None]:
    """Run with the logging a fresh worker process has, and put every process-global piece back afterwards:
    the record factory, the levels `logs.configure` changes, the `app.request` handler and Sentry."""
    names = ("httpx", "httpcore", "hpack", "app.request", "uvicorn.access")
    levels = {n: logging.getLogger(n).level for n in names}
    disabled = logging.getLogger("uvicorn.access").disabled
    handlers = list(logging.getLogger("app.request").handlers)
    previous_client = sentry_sdk.get_client()
    sentry_sdk.init(dsn=None)
    with logging_as_the_worker_has_it():
        try:
            yield
        finally:
            sentry_sdk.get_global_scope().set_client(previous_client)
            for n, level in levels.items():
                logging.getLogger(n).setLevel(level)
            logging.getLogger("uvicorn.access").disabled = disabled
            logging.getLogger("app.request").handlers[:] = handlers


# --- A1. the processes start with scrubbing and an alert channel ---------------------------------------------------


def test_control_configure_installs_scrubbing_and_init_sentry_starts_the_client(clean_process_logging: None) -> None:
    """The checks used below can pass: the API's own two calls make them true."""
    assert not scrubbing_installed(), "setup: the clean state has no scrubbing"
    assert not sentry_sdk.get_client().dsn is not None, "setup: the clean state has no Sentry"
    logs.configure([])
    logs.init_sentry("https://public@sentry.invalid/1", environment="test")
    assert scrubbing_installed()
    assert sentry_sdk.get_client().dsn is not None


def _entrypoint_env(monkeypatch: pytest.MonkeyPatch, api_dsn: str, worker_dsn: str) -> None:
    monkeypatch.setenv("DATABASE_URL", api_dsn)
    monkeypatch.setenv("DATABASE_URL_DIRECT", worker_dsn)
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)  # not a deployed cell: the recorded virus scanner


def _run_entrypoint(which: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real `main()` of the process, with the endless parts replaced: the worker loop returns at once and the
    cron run has no schedules to run (the shared test database must not have its daily jobs run)."""
    import app.worker.main as worker_main
    import app.worker.schedules as worker_schedules

    async def no_loop(self: JobWorker, *, concurrency: int = 1, install_signal_handlers: bool = False) -> None:
        return None

    async def no_schedules(worker: JobWorker, today: object) -> tuple[dict[str, int], list[str]]:
        return {}, []

    monkeypatch.setattr(JobWorker, "run", no_loop)
    monkeypatch.setattr(worker_schedules, "run_schedules", no_schedules)
    if which == "worker":
        worker_main.main()
        return
    with pytest.raises(SystemExit) as stopped:
        worker_schedules.main()
    assert stopped.value.code == 0


@pytest.mark.parametrize(
    "entrypoint",
    [
        pytest.param(
            "worker",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="BG-01: app/worker/main.py never calls logs.configure(), so the worker process logs unscrubbed",
            ),
        ),
        pytest.param(
            "cron",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="BG-01: app/worker/schedules.py never calls logs.configure() (it only runs logging.basicConfig)",
            ),
        ),
    ],
)
def test_a_the_worker_processes_install_the_scrubbing_log_factory(
    entrypoint: str,
    api_dsn: str,
    worker_dsn: str,
    clean_process_logging: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _entrypoint_env(monkeypatch, api_dsn, worker_dsn)
    _run_entrypoint(entrypoint, monkeypatch)
    assert scrubbing_installed(), "a log line with an e-mail address left the process unscrubbed"


@pytest.mark.parametrize(
    "entrypoint",
    [
        pytest.param(
            "worker",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="BG-02: app/worker/main.py never calls logs.init_sentry(), so _dead_letter() sends nothing",
            ),
        ),
        pytest.param(
            "cron",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="BG-02: app/worker/schedules.py never calls logs.init_sentry(): a failed schedule raises no alert",  # noqa: E501
            ),
        ),
    ],
)
def test_a_the_worker_processes_start_error_reporting_when_a_dsn_is_set(
    entrypoint: str,
    api_dsn: str,
    worker_dsn: str,
    clean_process_logging: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _entrypoint_env(monkeypatch, api_dsn, worker_dsn)
    _run_entrypoint(entrypoint, monkeypatch)
    assert sentry_sdk.get_client().dsn is not None, "SENTRY_DSN is set but the process never started Sentry"


# --- A2. a failing handler, sweep or schedule leaves no canary in any log ------------------------------------------


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


def _failing_kinds(registry: JobRegistry) -> list[str]:
    @registry.kind("zz_boom")
    async def boom(ctx: JobContext) -> None:
        raise RuntimeError(CANARY)

    @registry.kind("zz_check_violation")
    async def check_violation(ctx: JobContext) -> None:
        async with ctx.transaction() as conn:  # the step code must be lower case: the error's DETAIL echoes the row
            await conn.execute(
                "insert into app.job_steps (job_id, case_id, step) values (%s, %s, %s)",
                (ctx.job_id, ctx.case_id, CANARY),
            )

    @registry.kind("zz_unique_violation")
    async def unique_violation(ctx: JobContext) -> None:
        async with ctx.transaction() as conn:  # "Key (job_id, step)=(<id>, <canary>) already exists."
            for _ in range(2):
                await conn.execute(
                    "insert into app.job_steps (job_id, case_id, step) values (%s, %s, %s)",
                    (ctx.job_id, ctx.case_id, CANARY_LOWER),
                )

    return ["zz_boom", "zz_check_violation", "zz_unique_violation"]


async def _fail_every_kind(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    case: tuple[uuid.UUID, uuid.UUID],
) -> tuple[list[uuid.UUID], str]:
    expert, case_id = case
    registry = isolated_registry()
    kinds = _failing_kinds(registry)
    jobs: list[uuid.UUID] = []
    async with api_db.request(scope_for(expert)) as conn:
        for kind in kinds:
            jobs.append(await registry.enqueue(conn, case_id, kind))
    with capturing_all_logs() as captured:
        async with open_worker(registry, worker_db, worker_dsn, max_attempts=3) as worker:
            done = await run_until_finished(worker, admin, jobs)
    assert set(done.values()) == {"failed"}, done
    for job in jobs:  # the handlers really ran every attempt: the canary was in flight
        assert admin.execute("select attempts, error_code from app.jobs where id = %s", (job,)).fetchone() == (
            3,
            "job_error",
        )
    return jobs, captured.text


async def test_a_a_handler_error_carrying_text_reaches_no_log_line_traceback_or_record_attribute(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    case: tuple[uuid.UUID, uuid.UUID],
    clean_process_logging: None,
) -> None:
    assert not scrubbing_installed(), "setup: this is the logging a worker really has"
    _, text = await _fail_every_kind(api_db, worker_db, worker_dsn, admin, case)
    # the capture saw the worker's own failure lines, so an empty result is not an empty capture
    assert "failed on attempt" in text and "zz_boom" in text
    for needle in FORBIDDEN:
        assert needle not in text, "a handler's error text reached the log"


async def test_a_a_log_capture_would_see_text_if_the_worker_logged_it(clean_process_logging: None) -> None:
    """Control for the capture: a line that does carry the canary is found."""
    with capturing_all_logs() as captured:
        logging.getLogger("app.jobs").warning("synthetic %s", CANARY)
        try:
            raise RuntimeError(CANARY)
        except RuntimeError:
            logging.getLogger("procrastinate.worker").exception("synthetic failure")
    assert CANARY in captured.text


async def test_a_a_failing_sweep_and_a_failing_schedule_reach_no_log_line_traceback_or_record_attribute(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    clean_process_logging: None,
) -> None:
    registry = isolated_registry()
    ran: list[str] = []
    suffix = uuid.uuid4().hex[:8]
    sweep, schedule = f"zz_sweep_{suffix}", f"zz_schedule_{suffix}"

    @registry.sweep(sweep)
    async def failing_sweep(conn: psycopg.AsyncConnection) -> None:
        ran.append("sweep")
        raise RuntimeError(CANARY)

    @registry.schedule(schedule)
    async def failing_schedule(conn: psycopg.AsyncConnection, day: object) -> None:
        ran.append("schedule")
        raise psycopg.errors.UniqueViolation(CANARY)

    with capturing_all_logs() as captured:
        async with open_worker(registry, worker_db, worker_dsn) as worker:
            # what the worker's own housekeeping loop does every round, then what the cron service does
            assert await worker.run_sweeps() == 0
            with pytest.raises(psycopg.errors.UniqueViolation):
                await worker.catch_up(schedule, datetime.now(UTC).date())
            from app.worker.schedules import run_schedules

            counts, failed = await run_schedules(worker, datetime.now(UTC).date())
    assert ran.count("sweep") == 1 and ran.count("schedule") == 2, ran  # the handlers really ran and raised
    assert failed == [schedule]
    assert f"worker sweep {sweep} failed" in captured.text  # the capture saw the worker's own lines
    for needle in FORBIDDEN:
        assert needle not in captured.text


async def test_a_the_housekeeping_loop_logs_a_failing_sweep_and_schedule_without_the_text(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, clean_process_logging: None
) -> None:
    """The same, through the loop the worker actually runs (`JobWorker.run` starts `_housekeeping`)."""
    import asyncio

    registry = isolated_registry()
    ran: list[str] = []
    suffix = uuid.uuid4().hex[:8]

    @registry.sweep(f"zz_sweep_{suffix}")
    async def failing_sweep(conn: psycopg.AsyncConnection) -> None:
        ran.append("sweep")
        raise RuntimeError(CANARY)

    @registry.schedule(f"zz_schedule_{suffix}")
    async def failing_schedule(conn: psycopg.AsyncConnection, day: object) -> None:
        ran.append("schedule")
        raise RuntimeError(CANARY)

    with capturing_all_logs() as captured:
        async with open_worker(registry, worker_db, worker_dsn) as worker:
            task = asyncio.create_task(worker.run(concurrency=1))
            for _ in range(200):
                if {"sweep", "schedule"} <= set(ran):
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    assert {"sweep", "schedule"} <= set(ran)
    assert "worker housekeeping failed" in captured.text
    for needle in FORBIDDEN:
        assert needle not in captured.text


# --- A3. nor in the job tables or the queue's own tables ----------------------------------------------------------


async def test_a_a_failed_job_leaves_no_text_in_the_job_tables_or_the_queue_tables(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    case: tuple[uuid.UUID, uuid.UUID],
    clean_process_logging: None,
) -> None:
    jobs, _ = await _fail_every_kind(api_db, worker_db, worker_dsn, admin, case)
    ids = [str(j) for j in jobs]
    sources = {
        "app.jobs": "select to_jsonb(j)::text from app.jobs j where j.id::text = any(%s)",
        "app.job_steps": "select to_jsonb(s)::text from app.job_steps s where s.job_id::text = any(%s)",
        "procrastinate_jobs": "select to_jsonb(p)::text from procrastinate.procrastinate_jobs p"
        " where p.args ->> 'job_id' = any(%s)",
        "procrastinate_events": "select to_jsonb(e)::text from procrastinate.procrastinate_events e"
        " join procrastinate.procrastinate_jobs p on p.id = e.job_id where p.args ->> 'job_id' = any(%s)",
    }
    seen = {name: [r[0] for r in admin.execute(query, (ids,)).fetchall()] for name, query in sources.items()}
    # control: the scan found the rows it is meant to look in
    assert len(seen["app.jobs"]) == 3 and seen["procrastinate_jobs"] and seen["procrastinate_events"]
    for name, rows in seen.items():
        for needle in FORBIDDEN:
            assert not any(needle in row for row in rows), f"{name} holds the canary"
