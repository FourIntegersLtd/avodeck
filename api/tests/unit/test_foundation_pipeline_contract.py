"""The pipeline's typed entry point (contracts only grow).

Slice A registers `process_pages(conn, case_id, page_ids)`; slices B (rotation, transcription) and G
(new records) call it through the contract without importing slice A.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from psycopg import AsyncConnection

from app.core.contracts import pipeline

pytestmark = pytest.mark.anyio

JOB = uuid.uuid4()


async def fake_process_pages(conn: AsyncConnection, case_id: uuid.UUID, page_ids: Sequence[uuid.UUID]) -> uuid.UUID:
    fake_process_pages.calls.append((conn, case_id, tuple(page_ids)))  # type: ignore[attr-defined]
    return JOB


fake_process_pages.calls = []  # type: ignore[attr-defined]


async def test_calling_before_anything_is_registered_is_refused() -> None:
    entry = pipeline.EntryPoint[pipeline.ProcessPages]("process_pages")
    with pytest.raises(pipeline.EntryPointMissing):
        entry.get()


async def test_the_registered_implementation_receives_the_call() -> None:
    case_id, pages = uuid.uuid4(), [uuid.uuid4(), uuid.uuid4()]
    conn = object()
    with pipeline.process_pages_entry.override(fake_process_pages):
        assert await pipeline.process_pages(conn, case_id, pages) == JOB  # type: ignore[arg-type]
    assert fake_process_pages.calls[-1] == (conn, case_id, tuple(pages))  # type: ignore[attr-defined]


async def test_only_one_slice_may_implement_an_entry_point() -> None:
    entry = pipeline.EntryPoint[pipeline.ProcessPages]("process_pages")
    assert entry.register(fake_process_pages) is fake_process_pages
    assert entry.register(fake_process_pages) is fake_process_pages, "registering the same one again is harmless"

    async def another(conn: AsyncConnection, case_id: uuid.UUID, page_ids: Sequence[uuid.UUID]) -> uuid.UUID:
        return JOB

    with pytest.raises(pipeline.EntryPointTaken):
        entry.register(another)


async def test_an_override_is_undone_afterwards() -> None:
    entry = pipeline.EntryPoint[pipeline.ProcessPages]("process_pages")
    with entry.override(fake_process_pages):
        assert entry.get() is fake_process_pages
    with pytest.raises(pipeline.EntryPointMissing):
        entry.get()


def test_the_module_level_registration_helper_is_the_decorator_slice_a_uses() -> None:
    assert pipeline.register_process_pages.__name__ == "register_process_pages"
    assert pipeline.PROCESS_PAGES == "process_pages", "the job kinds are unchanged"


async def test_in_transaction_subscribers_get_the_connection_in_order_and_failures_propagate() -> None:
    from app.core.contracts import events

    bus = events.EventBus()
    seen: list[tuple[str, object]] = []
    event = events.DocumentUploaded(case_id=uuid.uuid4(), document_id=uuid.uuid4())
    assert event.appended is False

    @bus.subscribe_in_transaction(events.DocumentUploaded)
    async def first(conn: AsyncConnection, e: events.DocumentUploaded) -> None:
        seen.append(("first", conn))

    @bus.subscribe_in_transaction(events.DocumentUploaded)
    async def second(conn: AsyncConnection, e: events.DocumentUploaded) -> None:
        seen.append(("second", conn))
        raise RuntimeError("subscriber failed")

    conn = object()
    with pytest.raises(RuntimeError):
        await bus.publish(conn, event)  # type: ignore[arg-type]
    assert seen == [("first", conn), ("second", conn)]
    bus.unsubscribe(events.DocumentUploaded, second)
    await bus.publish(conn, event)  # type: ignore[arg-type]
    assert [name for name, _ in seen] == ["first", "second", "first"]
