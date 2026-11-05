"""A destroyed case key is forgotten by every process within a second, not after five minutes (scalability PR 15).

`app.destroy_case_key` sends `pg_notify('case_key_destroyed', '<case id>')` (the id and nothing else, rule 6). Each
process's `CaseNotifier` hears it and tells the key cache and the reader's search cache to drop that case. If the
listener is down, a cached key is checked against `case_keys.destroyed_at` once it is 30 seconds old, so the bound
without any listener is 30 seconds, never 5 minutes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from time import monotonic

import psycopg
import pytest

from app.core.crypto import REVALIDATE_AFTER, CaseCrypto, CaseKeyDestroyed, LocalKeyProvider
from app.core.db import Database
from app.core.key_events import KeyEvents
from app.core.notify import KEY_CHANNEL, LISTENER_APPLICATION_NAME, CaseNotifier
from app.features.reader import search_cache
from app.features.reader.search_cache import CaseWords
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SECRET = b"synthetic page text, not about anyone"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=1)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def expert_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return expert, seed.case(org, expert)


async def _process(api_dsn: str, provider: LocalKeyProvider) -> tuple[CaseCrypto, CaseNotifier]:
    """One stand-in 'process': its own key cache, its own listener, its own registry."""
    events = KeyEvents()
    crypto = CaseCrypto(provider, events=events)
    notifier = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=0.2, key_events=events)
    await notifier.start()
    await notifier.wait_ready(10)
    return crypto, notifier


async def test_destroying_a_key_notifies_with_the_case_id_and_nothing_else(
    api_db: Database,
    worker_db: Database,
    admin_dsn: str,
    expert_case: tuple[uuid.UUID, uuid.UUID],
) -> None:
    expert, case_id = expert_case
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        await crypto.encrypt(conn, case_id, SECRET)
    with psycopg.connect(admin_dsn, autocommit=True) as listener:
        listener.execute(f"listen {KEY_CHANNEL}")
        async with worker_db.job(case_id) as conn:
            await crypto.destroy(conn, case_id)
        notes = list(listener.notifies(timeout=3, stop_after=1))
    assert [n.payload for n in notes] == [str(case_id)]
    assert KEY_CHANNEL == "case_key_destroyed"


async def test_process_b_stops_decrypting_within_a_second_of_process_a_destroying_the_key(
    api_db: Database,
    worker_db: Database,
    api_dsn: str,
    expert_case: tuple[uuid.UUID, uuid.UUID],
) -> None:
    expert, case_id = expert_case
    provider = LocalKeyProvider.generate()
    crypto_a, notifier_a = await _process(api_dsn, provider)
    crypto_b, notifier_b = await _process(api_dsn, provider)
    try:
        async with api_db.request(scope_for(expert)) as conn:
            blob = await crypto_a.encrypt(conn, case_id, SECRET)
        async with api_db.request(scope_for(expert)) as conn:
            assert await crypto_b.decrypt(conn, case_id, blob) == SECRET, "B now holds the key in its own cache"

        async with worker_db.job(case_id) as conn:
            await crypto_a.destroy(conn, case_id)

        deadline = monotonic() + 1.0
        refused = False
        while monotonic() < deadline and not refused:
            try:
                async with api_db.request(scope_for(expert)) as conn:
                    await crypto_b.decrypt(conn, case_id, blob)
            except CaseKeyDestroyed:
                refused = True
            else:
                await asyncio.sleep(0.05)
        assert refused, "process B went on decrypting from its cache after the key was destroyed"
    finally:
        await notifier_a.stop()
        await notifier_b.stop()


async def test_the_reader_search_cache_for_that_case_is_cleared_and_other_cases_are_kept(
    api_dsn: str, admin: psycopg.Connection
) -> None:
    destroyed, kept = uuid.uuid4(), uuid.uuid4()

    async def loader() -> CaseWords:
        return CaseWords(pages={}, sizes={})

    notifier = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=0.2)  # the shared registry
    await notifier.start()
    try:
        await notifier.wait_ready(10)
        await asyncio.sleep(0.1)  # the connect clears everything once; fill the cache after that
        search_cache.clear_all()
        await search_cache.get_words(destroyed, loader, version=())
        await search_cache.get_words(kept, loader, version=())
        admin.execute("select pg_notify(%s, %s)", (KEY_CHANNEL, str(destroyed)))
        deadline = monotonic() + 2
        while monotonic() < deadline and destroyed in search_cache.case_ids():
            await asyncio.sleep(0.05)
        assert destroyed not in search_cache.case_ids()
        assert kept in search_cache.case_ids()
    finally:
        await notifier.stop()
        search_cache.clear_all()


async def test_a_payload_that_is_not_a_case_id_on_the_key_channel_is_ignored(
    api_dsn: str, admin: psycopg.Connection
) -> None:
    events = KeyEvents()
    seen: list[uuid.UUID] = []
    events.subscribe(seen.append)
    notifier = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=0.2, key_events=events)
    await notifier.start()
    try:
        await notifier.wait_ready(10)
        case = uuid.uuid4()
        admin.execute("select pg_notify(%s, %s)", (KEY_CHANNEL, "not-a-uuid"))
        admin.execute("select pg_notify(%s, %s)", (KEY_CHANNEL, str(case)))
        deadline = monotonic() + 2
        while monotonic() < deadline and not seen:
            await asyncio.sleep(0.05)
        assert seen == [case], "the bad payload was skipped and the listener is still alive"
    finally:
        await notifier.stop()


async def test_every_cache_is_cleared_after_the_listener_reconnects(api_dsn: str, admin: psycopg.Connection) -> None:
    events = KeyEvents()
    cleared = {"all": 0}

    def on_all() -> None:
        cleared["all"] += 1

    events.subscribe(lambda _case: None, on_all)
    notifier = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=0.2, key_events=events)
    await notifier.start()
    try:
        await notifier.wait_ready(10)
        await asyncio.sleep(0.1)
        before = cleared["all"]
        admin.execute(
            "select count(pg_terminate_backend(pid)) from pg_stat_activity where application_name = %s",
            (LISTENER_APPLICATION_NAME,),
        )
        deadline = monotonic() + 5
        while monotonic() < deadline and cleared["all"] == before:
            await asyncio.sleep(0.05)
        assert cleared["all"] > before, "a destruction while the listener was away may have been missed"
    finally:
        await notifier.stop()


async def test_without_a_listener_a_cached_key_is_checked_once_it_is_thirty_seconds_old(
    api_db: Database,
    worker_db: Database,
    expert_case: tuple[uuid.UUID, uuid.UUID],
) -> None:
    assert REVALIDATE_AFTER == timedelta(seconds=30)
    expert, case_id = expert_case
    provider = LocalKeyProvider.generate()
    clock = Clock()
    crypto_a = CaseCrypto(provider, clock=clock, events=KeyEvents())
    crypto_b = CaseCrypto(provider, clock=clock, events=KeyEvents())  # no notifier anywhere: the listener is down
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto_a.encrypt(conn, case_id, SECRET)
    async with api_db.request(scope_for(expert)) as conn:
        assert await crypto_b.decrypt(conn, case_id, blob) == SECRET

    async with worker_db.job(case_id) as conn:
        await crypto_a.destroy(conn, case_id)

    clock.now += timedelta(seconds=20)
    async with api_db.request(scope_for(expert)) as conn:
        assert await crypto_b.decrypt(conn, case_id, blob) == SECRET, "inside the documented 30 second bound"

    clock.now += timedelta(seconds=11)
    with pytest.raises(CaseKeyDestroyed):
        async with api_db.request(scope_for(expert)) as conn:
            await crypto_b.decrypt(conn, case_id, blob)


async def test_a_live_key_checked_after_thirty_seconds_keeps_working_without_reasking_the_key_service(
    api_db: Database, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    provider = LocalKeyProvider.generate()
    clock = Clock()
    crypto = CaseCrypto(provider, clock=clock, events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_id, SECRET)
    provider.reachable = False  # the key service is down; the check is against the database only
    clock.now += timedelta(seconds=45)
    async with api_db.request(scope_for(expert)) as conn:
        assert await crypto.decrypt(conn, case_id, blob) == SECRET


async def test_destroy_in_this_process_tells_this_processs_subscribers_at_once(
    worker_db: Database, api_db: Database, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    events = KeyEvents()
    seen: list[uuid.UUID] = []
    events.subscribe(seen.append)
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=events)
    async with api_db.request(scope_for(expert)) as conn:
        await crypto.encrypt(conn, case_id, SECRET)
    async with worker_db.job(case_id) as conn:
        await crypto.destroy(conn, case_id)
    assert seen == [case_id]
