"""Slice A, the bundle pipeline: scanning, splitting, reading and sorting an uploaded bundle.

Importing this package registers its job kinds on the app's registry, subscribes to
`DocumentUploaded` and fills the `process_pages` entry point. `mount_on(app)` adds its routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core import jobs
from app.core.contracts.events import DocumentUploaded, events
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES, register_process_pages
from app.features.pipeline import repo, wiring
from app.features.pipeline.router import pipeline_router
from app.features.pipeline.service import SCAN_DOCUMENT, process_bundle, queue_pages, scan_document

__all__ = ["mount_on", "pipeline_router", "register", "wiring"]


def register(registry: jobs.JobRegistry) -> None:
    registry.kind(SCAN_DOCUMENT)(scan_document)
    registry.kind(PROCESS_BUNDLE)(process_bundle)
    registry.kind(PROCESS_PAGES)(process_bundle)


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
