"""Helpers for the page-batch tests: a run, its batches, its jobs and their queue rows, built with the admin
connection. Numbers and ids only (rule 6)."""

from __future__ import annotations

import uuid

import psycopg

from tests.support.seed import Seed

WAKE_PRIORITY = 20


def make_job(
    admin: psycopg.Connection,
    seed: Seed,
    case: uuid.UUID,
    *,
    kind: str = "process_bundle",
    status: str = "running",
    code: str | None = None,
    priority: int = 0,
) -> uuid.UUID:
    """A job in the given status, moved there by legal transitions."""
    job = seed.job(case, kind=kind)
    admin.execute("update app.jobs set priority = %s where id = %s", (priority, job))
    if status in ("running", "paused", "failed", "done"):
        admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    if status == "paused":
        admin.execute("update app.jobs set status = 'paused', error_code = %s where id = %s", (code, job))
    elif status == "failed":
        admin.execute(
            "update app.jobs set status = 'failed', error_code = %s where id = %s", (code or "zz_failed", job)
        )
    elif status == "done":
        admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    return job


def add_row(
    admin: psycopg.Connection,
    job: uuid.UUID,
    case: uuid.UUID,
    *,
    queue: str = "bulk",
    lock: str | None | bool = True,
    priority: int = 0,
    status: str = "todo",
    delay_seconds: int = 0,
) -> int:
    """A queue row for the job. `lock=True` means `case:<id>`; `None` or `False` means no lock."""
    lock_value = f"case:{case}" if lock is True else (lock or None)
    admin.execute("set search_path = procrastinate, public")  # the queue's triggers use bare type names
    row = admin.execute(
        "select (unnest(procrastinate_defer_jobs_v1(array[row(%s, 'run_job', %s, %s, null,"
        " jsonb_build_object('job_id', %s::text, 'case_id', %s::text), null)::procrastinate_job_to_defer_v1])))",
        (queue, priority, lock_value, str(job), str(case)),
    ).fetchone()
    assert row is not None
    row_id = int(row[0])
    if delay_seconds:
        admin.execute(
            "update procrastinate.procrastinate_jobs set scheduled_at = now() + make_interval(secs => %s)"
            " where id = %s",
            (delay_seconds, row_id),
        )
    if status in ("doing", "succeeded"):
        admin.execute("update procrastinate.procrastinate_jobs set status = 'doing' where id = %s", (row_id,))
    if status == "succeeded":
        admin.execute("update procrastinate.procrastinate_jobs set status = 'succeeded' where id = %s", (row_id,))
    admin.execute("reset search_path")
    return row_id


def add_run(
    admin: psycopg.Connection,
    case: uuid.UUID,
    parent: uuid.UUID,
    state: str = "ocr_waiting",
    *,
    window: int = 2,
    batch_pages: int = 100,
) -> uuid.UUID:
    admin.execute(
        'insert into app.pipeline_runs (job_id, case_id, state, batch_pages, "window") values (%s, %s, %s, %s, %s)',
        (parent, case, state, batch_pages, window),
    )
    return parent


def add_batch(
    admin: psycopg.Connection,
    run: uuid.UUID,
    case: uuid.UUID,
    seq: int,
    *,
    phase: str = "ocr",
    child: uuid.UUID | None = None,
    done: bool = False,
    pages: int = 100,
) -> uuid.UUID:
    row = admin.execute(
        "insert into app.pipeline_batches (run_job_id, case_id, phase, seq, first_page_no, last_page_no, page_count,"
        " child_job_id, completed_at) values (%s, %s, %s, %s, %s, %s, %s, %s, case when %s then now() end)"
        " returning id",
        (run, case, phase, seq, seq * pages + 1, seq * pages + pages, pages, child, done),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def child_job(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID, *, status: str = "queued", code: str | None = None
) -> uuid.UUID:
    return make_job(admin, seed, case, kind="read_page_batch", status=status, code=code)


def rows_of(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[str, str, int, str | None]]:
    """(queue, status, priority, lock) of every queue row of the job, oldest first."""
    return [
        (str(r[0]), str(r[1]), int(r[2]), r[3])
        for r in admin.execute(
            "select queue_name, status::text, priority, lock from procrastinate.procrastinate_jobs"
            " where args ->> 'job_id' = %s order by id",
            (str(job),),
        ).fetchall()
    ]


def job_state(admin: psycopg.Connection, job: uuid.UUID) -> tuple[str, str | None, int, bool]:
    row = admin.execute(
        "select status, error_code, attempts, requeued_stall from app.jobs where id = %s", (job,)
    ).fetchone()
    assert row is not None
    return (str(row[0]), row[1], int(row[2]), bool(row[3]))


def run_state(admin: psycopg.Connection, run: uuid.UUID) -> str:
    row = admin.execute("select state from app.pipeline_runs where job_id = %s", (run,)).fetchone()
    assert row is not None
    return str(row[0])
