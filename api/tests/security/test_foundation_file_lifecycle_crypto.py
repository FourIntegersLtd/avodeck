"""AUDIT-12 C1: what sits in storage can only be opened by the case it belongs to, for the place it belongs to.

Every "cannot open" below is paired with a positive control (the same blob opened with the right case and
purpose), so a decrypt that fails for every input cannot pass. Content is made up ("synthetic ...").

Recorded, not asserted as gaps:
- `app/core/crypto.py` documents a bounded stale-key window: with the change listener down, another process may
  keep a destroyed case's key for up to `REVALIDATE_AFTER` (30 s) (C1c below, marked POLICY).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.core.crypto import (
    BLOB_VERSION,
    NONCE_BYTES,
    REVALIDATE_AFTER,
    CaseCrypto,
    CaseKeyDestroyed,
    CaseKeyMissing,
    CiphertextInvalid,
    LocalKeyProvider,
)
from app.core.db import Database
from app.core.key_events import KeyEvents
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PLAIN = b"synthetic page 1 of a synthetic bundle"
TAG = 16


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

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
def two_cases(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return expert, seed.case(org, expert), seed.case(org, expert)


def _flip(blob: bytes, at: int) -> bytes:
    changed = bytearray(blob)
    changed[at] ^= 0x01
    return bytes(changed)


async def test_a_blob_opens_only_for_its_own_case_and_its_own_place(
    api_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, case_b = two_cases
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=KeyEvents())
    page_1 = f"cases/{case_a}/pages/{uuid.uuid4()}/image"
    page_2 = f"cases/{case_a}/pages/{uuid.uuid4()}/image"
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_a, PLAIN, purpose=page_1)
        await crypto.encrypt(conn, case_b, PLAIN, purpose=page_1)  # case B has a key of its own too
        assert await crypto.decrypt(conn, case_a, blob, purpose=page_1) == PLAIN, "positive control"
        for other_case, purpose in (
            (case_a, page_2),  # a sealed page moved to another page's place
            (case_a, ""),  # the place dropped
            (case_a, page_1.upper()),
            (case_b, page_1),  # the same place under another case's key
        ):
            with pytest.raises(CiphertextInvalid):
                await crypto.decrypt(conn, other_case, blob, purpose=purpose)
        # sealed with no place, it does not open for a place either
        bare = await crypto.encrypt(conn, case_a, PLAIN)
        assert await crypto.decrypt(conn, case_a, bare) == PLAIN, "positive control"
        with pytest.raises(CiphertextInvalid):
            await crypto.decrypt(conn, case_a, bare, purpose=page_1)


@pytest.mark.parametrize("size", [len(PLAIN), 200 * 1024], ids=["small", "large-off-loop"])
async def test_any_changed_byte_of_a_blob_is_refused(
    api_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID], size: int
) -> None:
    expert, case_a, _ = two_cases
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=KeyEvents())
    data = (PLAIN * (size // len(PLAIN) + 1))[:size]
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_a, data, purpose="p")
        assert len(blob) == 1 + NONCE_BYTES + size + TAG, "version, nonce, ciphertext, tag and nothing else"
        assert blob[:1] == BLOB_VERSION and data[:32] not in blob
        assert await crypto.decrypt(conn, case_a, blob, purpose="p") == data, "positive control"
        spots = [0, 1, NONCE_BYTES, NONCE_BYTES + 1, len(blob) // 2, len(blob) - TAG, len(blob) - 1]
        for at in spots:
            with pytest.raises(CiphertextInvalid):
                await crypto.decrypt(conn, case_a, _flip(blob, at), purpose="p")
        for broken in (b"", blob[:1], blob[: 1 + NONCE_BYTES], blob[:-1], blob + b"\0", blob[1:], b"\x02" + blob[1:]):
            with pytest.raises(CiphertextInvalid):
                await crypto.decrypt(conn, case_a, broken, purpose="p")


async def test_every_seal_uses_a_fresh_nonce_and_a_fresh_blob(
    api_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, _ = two_cases
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        blobs = [await crypto.encrypt(conn, case_a, PLAIN, purpose="same") for _ in range(500)]
        assert await crypto.decrypt(conn, case_a, blobs[-1], purpose="same") == PLAIN, "positive control"
    nonces = {b[1 : 1 + NONCE_BYTES] for b in blobs}
    assert len(nonces) == len(blobs), "a nonce was used twice under one key"
    assert len(set(blobs)) == len(blobs)


async def test_a_wrapped_key_is_bound_to_its_case_and_to_the_master_key(
    api_db: Database, admin: psycopg.Connection, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, case_b = two_cases
    provider = LocalKeyProvider.generate()
    crypto = CaseCrypto(provider, events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        await crypto.encrypt(conn, case_a, PLAIN)
        await crypto.encrypt(conn, case_b, PLAIN)
    wrapped = {
        r[0]: bytes(r[1])
        for r in admin.execute(
            "select case_id, wrapped_key from app.case_keys where case_id in (%s, %s)", (case_a, case_b)
        )
    }
    assert wrapped[case_a] != wrapped[case_b]
    assert provider.decrypt_data_key(wrapped[case_a], {"case_id": str(case_a)}), "positive control"
    with pytest.raises(CiphertextInvalid):
        provider.decrypt_data_key(wrapped[case_a], {"case_id": str(case_b)})  # one case's key row moved to another
    with pytest.raises(CiphertextInvalid):
        LocalKeyProvider.generate().decrypt_data_key(wrapped[case_a], {"case_id": str(case_a)})


async def test_a_wrapped_key_that_was_changed_or_swapped_never_yields_a_new_key(
    api_db: Database, admin: psycopg.Connection, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, case_b = two_cases
    provider = LocalKeyProvider.generate()
    async with api_db.request(scope_for(expert)) as conn:
        first = CaseCrypto(provider, events=KeyEvents())
        blob_a = await first.encrypt(conn, case_a, PLAIN, purpose="p")
        blob_b = await first.encrypt(conn, case_b, PLAIN, purpose="p")
        assert await CaseCrypto(provider, events=KeyEvents()).decrypt(conn, case_a, blob_a, purpose="p") == PLAIN

    row = admin.execute("select wrapped_key from app.case_keys where case_id = %s", (case_a,)).fetchone()
    assert row is not None
    wrapped_a = bytes(row[0])
    row_b = admin.execute("select wrapped_key from app.case_keys where case_id = %s", (case_b,)).fetchone()
    assert row_b is not None

    # one bit of the stored wrapped key changed, then case B's wrapped key placed on case A's row
    for broken in (_flip(wrapped_a, 20), bytes(row_b[0])):
        admin.execute("update app.case_keys set wrapped_key = %s where case_id = %s", (broken, case_a))
        async with api_db.request(scope_for(expert)) as conn:
            fresh = CaseCrypto(provider, events=KeyEvents())
            with pytest.raises(CiphertextInvalid):
                await fresh.decrypt(conn, case_a, blob_a, purpose="p")
            with pytest.raises(CiphertextInvalid):
                await fresh.encrypt(conn, case_a, PLAIN, purpose="p")  # it must not quietly make a new key
    stored = admin.execute("select wrapped_key from app.case_keys where case_id = %s", (case_a,)).fetchone()
    assert stored is not None and bytes(stored[0]) == bytes(row_b[0]), "the row was replaced by a new key"

    admin.execute("update app.case_keys set wrapped_key = %s where case_id = %s", (wrapped_a, case_a))
    async with api_db.request(scope_for(expert)) as conn:
        fresh = CaseCrypto(provider, events=KeyEvents())
        assert await fresh.decrypt(conn, case_a, blob_a, purpose="p") == PLAIN, "positive control: restored"
        assert await fresh.decrypt(conn, case_b, blob_b, purpose="p") == PLAIN


async def test_a_case_without_a_key_cannot_be_read_and_reading_never_makes_one(
    api_db: Database, admin: psycopg.Connection, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, case_b = two_cases
    crypto = CaseCrypto(LocalKeyProvider.generate(), events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_a, PLAIN, purpose="p")
        assert await crypto.decrypt(conn, case_a, blob, purpose="p") == PLAIN, "positive control"
        with pytest.raises(CaseKeyMissing):
            await crypto.decrypt(conn, case_b, blob, purpose="p")
    count = admin.execute("select count(*) from app.case_keys where case_id = %s", (case_b,)).fetchone()
    assert count == (0,), "decrypting made a key for a case that never had one"


async def test_after_destruction_nothing_decrypts_a_key_cannot_come_back_and_the_row_holds_no_key(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, case_b = two_cases
    provider = LocalKeyProvider.generate()
    crypto = CaseCrypto(provider, events=KeyEvents())
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_a, PLAIN, purpose="p")
        other = await crypto.encrypt(conn, case_b, PLAIN, purpose="p")
    async with worker_db.job(case_a) as conn:
        await crypto.destroy(conn, case_a)

    for reader in (crypto, CaseCrypto(provider, events=KeyEvents())):
        async with api_db.request(scope_for(expert)) as conn:
            with pytest.raises(CaseKeyDestroyed):
                await reader.decrypt(conn, case_a, blob, purpose="p")
            with pytest.raises(CaseKeyDestroyed):
                await reader.encrypt(conn, case_a, PLAIN, purpose="p")  # no second life under a new key
            assert await reader.decrypt(conn, case_b, other, purpose="p") == PLAIN, "positive control: case B lives"
    wrapped, destroyed_at = admin.execute(
        "select wrapped_key, destroyed_at from app.case_keys where case_id = %s", (case_a,)
    ).fetchone() or (b"?", None)
    assert wrapped is None and destroyed_at is not None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute("update app.case_keys set destroyed_at = null where case_id = %s", (case_a,))
    with pytest.raises(psycopg.Error):
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute("select app.destroy_case_key(%s)", (case_b,))  # not callable from a request
    live = admin.execute("select wrapped_key is not null from app.case_keys where case_id = %s", (case_b,)).fetchone()
    assert live == (True,), "another case's key was touched"


async def test_a_listening_process_drops_a_destroyed_key_at_once(
    api_db: Database, worker_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, _ = two_cases
    provider = LocalKeyProvider.generate()
    events = KeyEvents()
    clock = Clock()
    here = CaseCrypto(provider, events=events, clock=clock)
    there = CaseCrypto(provider, events=events, clock=clock)  # another process that hears the same message
    async with api_db.request(scope_for(expert)) as conn:
        blob = await here.encrypt(conn, case_a, PLAIN, purpose="p")
        assert await there.decrypt(conn, case_a, blob, purpose="p") == PLAIN, "positive control: warm"
    async with worker_db.job(case_a) as conn:
        await here.destroy(conn, case_a)
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await there.decrypt(conn, case_a, blob, purpose="p")


async def test_a_process_that_missed_the_message_still_refuses_after_the_revalidation_bound(
    api_db: Database, worker_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, _ = two_cases
    provider = LocalKeyProvider.generate()
    clock = Clock()
    here = CaseCrypto(provider, events=KeyEvents(), clock=clock)
    deaf = CaseCrypto(provider, events=KeyEvents(), clock=clock)  # listener down: never told
    async with api_db.request(scope_for(expert)) as conn:
        blob = await here.encrypt(conn, case_a, PLAIN, purpose="p")
        assert await deaf.decrypt(conn, case_a, blob, purpose="p") == PLAIN, "positive control: warm"
    async with worker_db.job(case_a) as conn:
        await here.destroy(conn, case_a)
    clock.now += REVALIDATE_AFTER + timedelta(seconds=1)
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await deaf.decrypt(conn, case_a, blob, purpose="p")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 POLICY C1c: a process whose change listener is down decrypts a destroyed case's data for up to "
        "REVALIDATE_AFTER (30 s) after destruction, because a warm cached key is only rechecked after that "
        "interval: app/core/crypto.py:47 and :217-229 (documented as the bound, PR 15)"
    ),
)
async def test_a_process_that_missed_the_message_refuses_straight_away(
    api_db: Database, worker_db: Database, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a, _ = two_cases
    provider = LocalKeyProvider.generate()
    clock = Clock()
    here = CaseCrypto(provider, events=KeyEvents(), clock=clock)
    deaf = CaseCrypto(provider, events=KeyEvents(), clock=clock)
    async with api_db.request(scope_for(expert)) as conn:
        blob = await here.encrypt(conn, case_a, PLAIN, purpose="p")
        assert await deaf.decrypt(conn, case_a, blob, purpose="p") == PLAIN
    async with worker_db.job(case_a) as conn:
        await here.destroy(conn, case_a)
    clock.now += timedelta(seconds=5)  # inside the bound
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await deaf.decrypt(conn, case_a, blob, purpose="p")
