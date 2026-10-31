"""Per-case envelope encryption (ADR 0010, architecture 9 "Encryption").

Each case has its own 256-bit data key. The cell's key provider wraps it (the local provider in
stage 1; AWS KMS in the home region from stage 2, behind the same `KeyProvider` interface) and only
the wrapped key is stored, in `case_keys`. Unwrapped keys are cached for at most 5 minutes. When the
provider cannot be reached and the cached key has expired, processing pauses: nothing falls back to
another key or region (fail closed, F24). Destroying a case's key (case deletion) makes everything
encrypted under it unreadable. That holds for what is stored under the key, not for copies made
before deletion: backups and Storage objects are unreadable only once the wrapped key is gone from
them too, and the upload job holds plaintext in memory (never on disk) while it checks a file.

`SecretBox` seals short server-side secrets (Supabase Auth tokens on a device session) the same
way, bound to their purpose and subject.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import AsyncConnection

from app.core.config import ConfigError
from app.core.errors import ProcessingPaused

KEY_BYTES = 32
NONCE_BYTES = 12
BLOB_VERSION = b"\x01"
SEALED_PREFIX = "av1:"
MAX_CACHE = timedelta(minutes=5)


class CryptoError(Exception):
    """Base for refusals. Messages never carry key material or plaintext."""


class CiphertextInvalid(CryptoError):
    """The ciphertext was altered, belongs to another case or purpose, or was made with another key."""


class CaseKeyDestroyed(CryptoError):
    """The case's key was destroyed (case deleted): its data can never be read again."""


class StoredFileChanged(CryptoError):
    """A stored file no longer matches the SHA-256 recorded when it was uploaded."""


class CaseKeyMissing(CryptoError):
    """The case has no key yet, so nothing of it was ever encrypted."""


class KeyServiceUnreachable(CryptoError):
    """The key provider did not answer."""


def _paused() -> ProcessingPaused:
    return ProcessingPaused("key_service_unreachable")


@dataclass(frozen=True)
class DataKey:
    plaintext: bytes = field(repr=False)
    wrapped: bytes = field(repr=False)


class KeyProvider(Protocol):
    """What the local provider and the KMS provider both offer. `context` is bound to the wrapped key."""

    @property
    def key_ref(self) -> str: ...

    def generate_data_key(self, context: Mapping[str, str]) -> DataKey: ...

    def decrypt_data_key(self, wrapped: bytes, context: Mapping[str, str]) -> bytes: ...

    def mac(self, data: bytes, *, purpose: str) -> bytes:
        """HMAC-SHA256 of `data` under a cell secret for `purpose` that never leaves the provider."""
        ...


def _aad(context: Mapping[str, str]) -> bytes:
    return json.dumps(dict(context), sort_keys=True, separators=(",", ":")).encode()


class LocalKeyProvider:
    """Stage 1 stand-in for KMS: a master key from the cell config wraps data keys with AES-GCM.

    `reachable` lets tests take the service away, as a KMS outage would.
    """

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != KEY_BYTES:
            raise ConfigError("the local master key must be 32 bytes")
        self._aead = AESGCM(master_key)
        self._key_ref = "local:" + hashlib.sha256(b"avodeck-key-ref" + master_key).hexdigest()[:16]
        self._mac_root = hmac.new(master_key, b"avodeck-mac", hashlib.sha256).digest()
        self.reachable = True

    @classmethod
    def generate(cls) -> LocalKeyProvider:
        return cls(AESGCM.generate_key(bit_length=KEY_BYTES * 8))

    def __repr__(self) -> str:
        return f"LocalKeyProvider(key_ref={self._key_ref!r})"

    @property
    def key_ref(self) -> str:
        return self._key_ref

    def _check(self) -> None:
        if not self.reachable:
            raise KeyServiceUnreachable("key service unreachable")

    def generate_data_key(self, context: Mapping[str, str]) -> DataKey:
        self._check()
        key = AESGCM.generate_key(bit_length=KEY_BYTES * 8)
        nonce = os.urandom(NONCE_BYTES)
        return DataKey(plaintext=key, wrapped=nonce + self._aead.encrypt(nonce, key, _aad(context)))

    def decrypt_data_key(self, wrapped: bytes, context: Mapping[str, str]) -> bytes:
        self._check()
        try:
            return self._aead.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], _aad(context))
        except (InvalidTag, ValueError) as exc:
            raise CiphertextInvalid("the data key cannot be unwrapped") from exc

    def mac(self, data: bytes, *, purpose: str) -> bytes:
        self._check()
        key = hmac.new(self._mac_root, purpose.encode(), hashlib.sha256).digest()
        return hmac.new(key, data, hashlib.sha256).digest()


def key_provider_from_env(env: Mapping[str, str] | None = None) -> KeyProvider:
    """`KEY_PROVIDER=local` with `LOCAL_MASTER_KEY` (32 bytes, base64). KMS arrives with the cloud cell."""
    source = os.environ if env is None else env
    kind = source.get("KEY_PROVIDER", "").strip()
    if kind != "local":
        raise ConfigError("KEY_PROVIDER must be 'local' until the KMS provider is configured")
    raw = source.get("LOCAL_MASTER_KEY", "").strip()
    if not raw:
        raise ConfigError("LOCAL_MASTER_KEY is not set")
    try:
        master = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("LOCAL_MASTER_KEY must be base64") from exc
    return LocalKeyProvider(master)


def _seal(key: bytes, data: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return BLOB_VERSION + nonce + AESGCM(key).encrypt(nonce, data, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if blob[:1] != BLOB_VERSION or len(blob) < 1 + NONCE_BYTES + 16:
        raise CiphertextInvalid("not an Avodeck ciphertext")
    try:
        return AESGCM(key).decrypt(blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :], aad)
    except InvalidTag as exc:
        raise CiphertextInvalid("ciphertext does not match its key") from exc


class CaseCrypto:
    """Encrypts and decrypts one case's data under that case's own key."""

    def __init__(
        self,
        provider: KeyProvider,
        *,
        ttl: timedelta = MAX_CACHE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if ttl > MAX_CACHE:
            raise ConfigError("unwrapped case keys may be cached for 5 minutes at most")
        self.provider = provider
        self.ttl = ttl
        self.clock = clock
        self._cache: dict[uuid.UUID, tuple[bytes, datetime]] = {}

    @staticmethod
    def _context(case_id: uuid.UUID) -> dict[str, str]:
        return {"case_id": str(case_id)}

    async def _key(self, conn: AsyncConnection, case_id: uuid.UUID, *, create: bool) -> bytes:
        now = self.clock()
        cached = self._cache.get(case_id)
        if cached is not None and cached[1] > now:
            return cached[0]
        self._cache.pop(case_id, None)
        cur = await conn.execute("select wrapped_key, destroyed_at from app.case_keys where case_id = %s", (case_id,))
        row = await cur.fetchone()
        try:
            if row is None:
                if not create:
                    raise CaseKeyMissing("the case has no key")
                key = await self._create(conn, case_id)
            elif row[1] is not None:
                raise CaseKeyDestroyed("the case's key was destroyed")
            else:
                key = self.provider.decrypt_data_key(bytes(row[0]), self._context(case_id))
        except KeyServiceUnreachable as exc:
            raise _paused() from exc
        self._cache[case_id] = (key, now + self.ttl)
        return key

    async def _create(self, conn: AsyncConnection, case_id: uuid.UUID) -> bytes:
        data_key = self.provider.generate_data_key(self._context(case_id))
        cur = await conn.execute(
            "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, %s)"
            " on conflict (case_id) do nothing returning case_id",
            (case_id, data_key.wrapped, self.provider.key_ref),
        )
        if await cur.fetchone() is not None:
            return data_key.plaintext
        return await self._key(conn, case_id, create=False)  # another process made it first

    @staticmethod
    def _case_aad(case_id: uuid.UUID, purpose: str) -> bytes:
        # Bound to the case and, when given, to where the blob belongs (e.g. its storage path).
        return case_id.bytes + (b"|" + purpose.encode() if purpose else b"")

    async def encrypt(self, conn: AsyncConnection, case_id: uuid.UUID, data: bytes, *, purpose: str = "") -> bytes:
        return _seal(await self._key(conn, case_id, create=True), data, self._case_aad(case_id, purpose))

    async def decrypt(self, conn: AsyncConnection, case_id: uuid.UUID, blob: bytes, *, purpose: str = "") -> bytes:
        return _open(await self._key(conn, case_id, create=False), blob, self._case_aad(case_id, purpose))

    async def destroy(self, conn: AsyncConnection, case_id: uuid.UUID) -> None:
        """Worker only, inside the case's own job (the database refuses anyone else)."""
        self._cache.pop(case_id, None)
        await conn.execute("select app.destroy_case_key(%s)", (case_id,))
        self._cache.pop(case_id, None)


class SecretBox:
    """Seals a short secret as `av1:<base64>` under a fresh data key bound to purpose and subject."""

    def __init__(self, provider: KeyProvider) -> None:
        self.provider = provider

    def seal(self, text: str, *, purpose: str, subject: str) -> str:
        context = {"purpose": purpose, "subject": subject}
        try:
            data_key = self.provider.generate_data_key(context)
        except KeyServiceUnreachable as exc:
            raise _paused() from exc
        body = _seal(data_key.plaintext, text.encode(), _aad(context))
        packed = len(data_key.wrapped).to_bytes(2, "big") + data_key.wrapped + body
        return SEALED_PREFIX + base64.urlsafe_b64encode(packed).decode()

    def open(self, sealed: str, *, purpose: str, subject: str) -> str:
        if not sealed.startswith(SEALED_PREFIX):
            raise CiphertextInvalid("not a sealed value")
        try:
            packed = base64.urlsafe_b64decode(sealed[len(SEALED_PREFIX) :])
        except (binascii.Error, ValueError) as exc:
            raise CiphertextInvalid("not a sealed value") from exc
        size = int.from_bytes(packed[:2], "big")
        wrapped, body = packed[2 : 2 + size], packed[2 + size :]
        context = {"purpose": purpose, "subject": subject}
        try:
            key = self.provider.decrypt_data_key(wrapped, context)
        except KeyServiceUnreachable as exc:
            raise _paused() from exc
        return _open(key, body, _aad(context)).decode()


@cache
def cell_key_provider() -> KeyProvider:
    """The process's key provider, from the cell config (read on first use)."""
    return key_provider_from_env()


@cache
def cell_secret_box() -> SecretBox:
    """The process's secret box, from the cell config (read on first use)."""
    return SecretBox(cell_key_provider())


def subject_key_hash(normalised: str, *, provider: KeyProvider | None = None) -> str:
    """Keyed hash (hex HMAC-SHA256 under the cell secret) of a normalised subject key, for the duplicate-case
    check. Callers build `normalised` with `pack.normalise_identifier()`; store and compare only the hash.
    Raises ProcessingPaused when the key service cannot be reached (fail closed)."""
    try:
        return (provider or cell_key_provider()).mac(normalised.encode(), purpose="subject_key").hex()
    except KeyServiceUnreachable as exc:
        raise _paused() from exc
