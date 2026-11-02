"""Shared hashing and chunking helpers, and the explicit uuid parsers."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.core.hashing import sha256_hex, sha256_stream
from app.core.iterkit import iter_chunks
from app.core.uuids import require_uuid, uuid_or_none


def test_sha256_hex_matches_hashlib() -> None:
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_sha256_stream_equals_the_hash_of_the_whole() -> None:
    pieces = [b"one", b"", b"two", b"three" * 1000]
    assert sha256_stream(pieces) == sha256_hex(b"".join(pieces))


def test_iter_chunks_splits_and_rejoins() -> None:
    data = bytes(range(250)) * 3
    pieces = list(iter_chunks(data, 100))
    assert [len(p) for p in pieces] == [100] * 7 + [50]
    assert b"".join(pieces) == data
    assert all(isinstance(p, bytes) for p in pieces)


def test_iter_chunks_of_nothing_is_nothing() -> None:
    assert list(iter_chunks(b"", 10)) == []


def test_iter_chunks_needs_a_positive_size() -> None:
    with pytest.raises(ValueError):
        list(iter_chunks(b"x", 0))


def test_uuid_or_none_never_raises() -> None:
    one = uuid.uuid4()
    assert uuid_or_none(str(one)) == one
    assert uuid_or_none(one) == one
    assert uuid_or_none("not a uuid") is None
    assert uuid_or_none(None) is None
    assert uuid_or_none(12) is None


def test_require_uuid_raises_on_garbage_and_passes_a_uuid_through() -> None:
    one = uuid.uuid4()
    assert require_uuid(str(one)) == one
    assert require_uuid(one) == one
    with pytest.raises(ValueError):
        require_uuid("not a uuid")
