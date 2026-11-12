"""Helpers for the background-job security probes (audit 15): hand-made queue rows, role connections, log capture."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg

from app.core import logs

CANARY = "ZZCANARY-PATIENT-TEXT-15"


def defer(
    admin: psycopg.Connection,
    queue: str,
    job: uuid.UUID,
    case: uuid.UUID,
    *,
    lock: str | None = None,
    priority: int = 0,
    scheduled_in_seconds: float | None = None,
) -> None:
    """Put a queue row on `queue` the way `app.enqueue_job` does, with the ids given (they need not agree)."""
    admin.execute("set search_path = procrastinate, public")
    try:
        admin.execute(
            "select procrastinate_defer_jobs_v1(array[row(%s, 'run_job', %s, %s, null,"
            " jsonb_build_object('job_id', %s::text, 'case_id', %s::text), null)::procrastinate_job_to_defer_v1])",
            (queue, priority, lock, str(job), str(case)),
        )
    finally:
        admin.execute("reset search_path")
    if scheduled_in_seconds is not None:
        admin.execute(
            "update procrastinate.procrastinate_jobs set scheduled_at = now() + make_interval(secs => %s)"
            " where queue_name = %s and args ->> 'job_id' = %s",
            (scheduled_in_seconds, queue, str(job)),
        )


def queue_rows(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[str, str]]:
    """(queue, status) of every queue row naming the job."""
    return admin.execute(
        "select queue_name, status::text from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s order by id",  # noqa: E501
        (str(job),),
    ).fetchall()


def job_row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[Any, ...]:
    found = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert found is not None
    return found


@contextlib.contextmanager
def role_connection(dsn: str) -> Iterator[psycopg.Connection]:
    """A connection as the given role with one open transaction that is always rolled back."""
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=20)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def attempt(conn: psycopg.Connection, query: str, params: Any = None) -> tuple[str | None, int, list[tuple[Any, ...]]]:
    """Run one statement in a savepoint: (SQLSTATE if refused, rows touched, rows returned)."""
    try:
        with conn.transaction():
            cur = conn.execute(query, params)
            rows = cur.fetchall() if cur.description else []
            return None, cur.rowcount, rows
    except psycopg.Error as exc:
        return exc.sqlstate or "?", 0, []


class LogCapture(logging.Handler):
    """Everything the root logger sees at DEBUG, formatted the way a plain log line is (tracebacks included) plus
    the record's own attributes, so a value hidden in `extra` shows up as well."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []
        self.records = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.records += 1
        self.lines.append(self.format(record))
        self.lines.append(repr(record.__dict__))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@contextlib.contextmanager
def capturing_all_logs() -> Iterator[LogCapture]:
    handler = LogCapture()
    root = logging.getLogger()
    names = ("", "procrastinate", "app", "psycopg", "psycopg.pool")
    levels = {n: logging.getLogger(n).level for n in names}
    root.addHandler(handler)
    for n in names:
        logging.getLogger(n).setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        for n, level in levels.items():
            logging.getLogger(n).setLevel(level)


@contextlib.contextmanager
def logging_as_the_worker_has_it() -> Iterator[None]:
    """The process-global logging state a worker really starts with: no scrubbing record factory (only the API's
    `create_app` installs one). Saved and put back so no other test sees the difference."""
    saved_factory = logging.getLogRecordFactory()
    saved_base = logs._base_factory  # noqa: SLF001
    saved_finders = logs._finders  # noqa: SLF001
    if saved_base is not None:
        logging.setLogRecordFactory(saved_base)
        logs._base_factory = None  # noqa: SLF001
    try:
        yield
    finally:
        logging.setLogRecordFactory(saved_factory)
        logs._base_factory = saved_base  # noqa: SLF001
        logs._finders = saved_finders  # noqa: SLF001


def scrubbing_installed() -> bool:
    """True when a log line carrying an e-mail address comes out without it (the record factory scrubs it)."""
    seen: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    log = logging.getLogger("zz.audit15.scrub_probe")
    grab = _Grab(logging.DEBUG)
    log.addHandler(grab)
    log.setLevel(logging.DEBUG)
    log.propagate = False
    try:
        log.warning("contact zz.probe.person@example.invalid please")
    finally:
        log.removeHandler(grab)
    return bool(seen) and "zz.probe.person@example.invalid" not in seen[0]


async def wait_for(condition: Callable[[], bool], *, timeout: float = 20, step: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(step)
