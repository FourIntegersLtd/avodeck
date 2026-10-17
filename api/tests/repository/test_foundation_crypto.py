"""Per-case envelope encryption (ADR 0010, architecture 9 "Encryption", failure mode F24).

Each case has its own data key, wrapped by the key provider and stored in `case_keys`. The local
provider used here sits behind the same interface the KMS provider will use. Unwrapped keys are
cached for at most 5 minutes; when the provider cannot be reached and the cache has expired,
processing pauses (fail closed). Destroying a case's key makes its ciphertext unreadable for good.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app.core.config import ConfigError
from app.core.crypto import (
    CaseCrypto,
    CaseKeyDestroyed,
    CiphertextInvalid,
    LocalKeyProvider,
    SecretBox,
    key_provider_from_env,
)
from app.core.db import Database
from app.core.errors import ProcessingPaused
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SECRET = b"synthetic page text, not about anyone"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

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


async def test_a_case_round_trips_under_its_own_key(
    api_db: Database, admin: psycopg.Connection, seed: Seed, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_a = expert_case
    case_b = seed.case(seed.org(members={expert: "owner"}), expert)
    provider = LocalKeyProvider.generate()
    crypto = CaseCrypto(provider)
    async with api_db.request(scope_for(expert)) as conn:
        blob_a = await crypto.encrypt(conn, case_a, SECRET)
        blob_b = await crypto.encrypt(conn, case_b, SECRET)
        assert SECRET not in blob_a and blob_a != blob_b
        assert await crypto.decrypt(conn, case_a, blob_a) == SECRET
        with pytest.raises(CiphertextInvalid):
            await crypto.decrypt(conn, case_b, blob_a)

    rows = admin.execute(
        "select case_id, wrapped_key, key_ref from app.case_keys where case_id in (%s, %s)", (case_a, case_b)
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] != rows[1][1]
    assert {r[2] for r in rows} == {provider.key_ref}

    async with api_db.request(scope_for(expert)) as conn:
        assert await CaseCrypto(provider).decrypt(conn, case_a, blob_a) == SECRET, "a fresh process unwraps the key"


async def test_another_master_key_cannot_read_the_case(
    api_db: Database, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    async with api_db.request(scope_for(expert)) as conn:
        blob = await CaseCrypto(LocalKeyProvider.generate()).encrypt(conn, case_id, SECRET)
        with pytest.raises(CiphertextInvalid):
            await CaseCrypto(LocalKeyProvider.generate()).decrypt(conn, case_id, blob)


async def test_case_keys_are_private_to_the_case_and_never_rewritten(
    api_db: Database, seed: Seed, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    async with api_db.request(scope_for(expert)) as conn:
        await CaseCrypto(LocalKeyProvider.generate()).encrypt(conn, case_id, SECRET)
    async with api_db.request(scope_for(seed.person())) as conn:
        cur = await conn.execute("select count(*) from app.case_keys where case_id = %s", (case_id,))
        assert await cur.fetchone() == (0,)
    for statement in ("update app.case_keys set wrapped_key = null", "delete from app.case_keys"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with api_db.request(scope_for(expert)) as conn:
                await conn.execute(statement + " where case_id = %s", (case_id,))


async def test_after_the_key_is_destroyed_nothing_decrypts(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    provider = LocalKeyProvider.generate()
    crypto = CaseCrypto(provider)
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_id, SECRET)

    with pytest.raises(psycopg.Error):
        async with api_db.request(scope_for(expert)) as conn:
            await crypto.destroy(conn, case_id)

    async with worker_db.job(case_id) as conn:
        await crypto.destroy(conn, case_id)

    for reader in (crypto, CaseCrypto(provider)):
        with pytest.raises(CaseKeyDestroyed):
            async with api_db.request(scope_for(expert)) as conn:
                await reader.decrypt(conn, case_id, blob)
    with pytest.raises(CaseKeyDestroyed):
        async with api_db.request(scope_for(expert)) as conn:
            await crypto.encrypt(conn, case_id, SECRET)

    wrapped, destroyed_at = admin.execute(
        "select wrapped_key, destroyed_at from app.case_keys where case_id = %s", (case_id,)
    ).fetchone() or (None, None)
    assert wrapped is None and destroyed_at is not None
    actions = admin.execute("select action from app.audit_log where case_id = %s", (case_id,)).fetchall()
    assert ("case.key_destroyed",) in actions


async def test_an_unreachable_key_service_pauses_processing_once_the_cache_expires(
    api_db: Database, admin: psycopg.Connection, seed: Seed, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = expert_case
    provider = LocalKeyProvider.generate()
    clock = Clock()
    crypto = CaseCrypto(provider, ttl=timedelta(minutes=5), clock=clock)
    async with api_db.request(scope_for(expert)) as conn:
        blob = await crypto.encrypt(conn, case_id, SECRET)

    provider.reachable = False
    clock.now += timedelta(minutes=4)
    async with api_db.request(scope_for(expert)) as conn:
        assert await crypto.decrypt(conn, case_id, blob) == SECRET, "a cached key keeps working"

    clock.now += timedelta(minutes=2)
    with pytest.raises(ProcessingPaused) as paused:
        async with api_db.request(scope_for(expert)) as conn:
            await crypto.decrypt(conn, case_id, blob)
    assert paused.value.status == 503 and paused.value.reason == "key_service_unreachable"

    other = seed.case(seed.org(members={expert: "owner"}), expert)
    with pytest.raises(ProcessingPaused):
        async with api_db.request(scope_for(expert)) as conn:
            await crypto.encrypt(conn, other, SECRET)
    assert admin.execute("select count(*) from app.case_keys where case_id = %s", (other,)).fetchone() == (0,)

    provider.reachable = True
    async with api_db.request(scope_for(expert)) as conn:
        assert await crypto.decrypt(conn, case_id, blob) == SECRET


def test_keys_are_never_cached_longer_than_five_minutes() -> None:
    with pytest.raises(ConfigError):
        CaseCrypto(LocalKeyProvider.generate(), ttl=timedelta(minutes=5, seconds=1))


def test_the_key_provider_comes_from_the_cell_config() -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    provider = key_provider_from_env({"KEY_PROVIDER": "local", "LOCAL_MASTER_KEY": key})
    assert isinstance(provider, LocalKeyProvider)
    assert key not in repr(provider)
    for env in (
        {"KEY_PROVIDER": "local"},
        {"KEY_PROVIDER": "local", "LOCAL_MASTER_KEY": base64.b64encode(b"short").decode()},
        {"KEY_PROVIDER": "plaintext"},
        {},
    ):
        with pytest.raises(ConfigError):
            key_provider_from_env(env)


def test_a_secret_box_seals_short_secrets_bound_to_their_purpose_and_subject() -> None:
    box = SecretBox(LocalKeyProvider.generate())
    subject = str(uuid.uuid4())
    sealed = box.seal("a-made-up-token", purpose="session.auth", subject=subject)
    assert sealed.startswith("av1:") and "a-made-up-token" not in sealed
    assert box.open(sealed, purpose="session.auth", subject=subject) == "a-made-up-token"
    for purpose, other in (("session.auth", str(uuid.uuid4())), ("other.purpose", subject)):
        with pytest.raises(CiphertextInvalid):
            box.open(sealed, purpose=purpose, subject=other)
    with pytest.raises(CiphertextInvalid):
        box.open("a-made-up-token", purpose="session.auth", subject=subject)
