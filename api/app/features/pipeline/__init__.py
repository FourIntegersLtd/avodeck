"""Slice A, the bundle pipeline: scanning, splitting, reading and sorting an uploaded bundle.

Importing this package registers its job kinds on the app's registry, subscribes to
`DocumentUploaded` and fills the `process_pages` and `extract_page_text` entry points. `mount_on(app)` adds its routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core import jobs
from app.core.contracts.events import DocumentUploaded, events
from app.core.contracts.fact_dates import register_resolve_fact_event_date
from app.core.contracts.pipeline import (
    PROCESS_BUNDLE,
    PROCESS_PAGES,
    register_extract_page_text,
    register_process_pages,
)
from app.core.jobs import RESUMABLE_PAUSES
from app.features.pipeline import date_repair, record_gaps_reader, repo, wiring
from app.features.pipeline.router import pipeline_router
from app.features.pipeline.service import (
    EXTRACT_ENTRIES,
    PURGE_QUARANTINED,
    READ_PAGE_BATCH,
    REPAIR_FACT_DATES,
    REPROCESS_CASE,
    RETRY_UNREAD_PAGES,
    SCAN_DOCUMENT,
    extract_entries,
    process_bundle,
    purge_quarantined,
    queue_page_text,
    queue_pages,
    read_page_batch,
    repair_fact_dates,
    reprocess_case,
    retry_unread_pages,
    scan_document,
)

__all__ = ["mount_on", "pipeline_router", "record_gaps_reader", "register", "wiring"]


async def heal_runs(conn: AsyncConnection) -> None:
    """The housekeeping round for page-batch runs: re-queue what lost its queue row, wake parents, fire the next run
    (`app.pipeline_heal_runs`). The codes that may resume are the worker's own list."""
    await repo.heal_runs(conn, sorted(RESUMABLE_PAUSES))


def register(registry: jobs.JobRegistry) -> None:
    registry.kind(SCAN_DOCUMENT)(scan_document)
    registry.kind(PROCESS_BUNDLE)(process_bundle)
    registry.kind(READ_PAGE_BATCH, changes_case=False)(read_page_batch)
    registry.sweep("pipeline_heal_runs")(heal_runs)
    registry.kind(PROCESS_PAGES)(process_bundle)
    registry.kind(REPROCESS_CASE)(reprocess_case)
    registry.kind(REPAIR_FACT_DATES)(repair_fact_dates)
    registry.kind(EXTRACT_ENTRIES)(extract_entries)
    registry.kind(RETRY_UNREAD_PAGES)(retry_unread_pages)
    registry.schedule(PURGE_QUARANTINED)(purge_quarantined)


def mount_on(app: FastAPI) -> None:
    from app.main import mount

    mount(app, pipeline_router)


@events.subscribe_in_transaction(DocumentUploaded)
async def queue_scan(conn: AsyncConnection, event: DocumentUploaded) -> None:
    """Completing an upload queues its virus scan in the same transaction; later records join the
    next bundle version (shared by every file appended before processing reaches them)."""
    if event.appended:
        await repo.join_next_version(conn, event.case_id, event.document_id)
    await wiring.registry().enqueue(conn, event.case_id, SCAN_DOCUMENT)


if SCAN_DOCUMENT not in jobs.registry.kinds:
    register(jobs.registry)
register_process_pages(queue_pages)
register_extract_page_text(queue_page_text)
register_resolve_fact_event_date(date_repair.resolve_fact_event_date)
