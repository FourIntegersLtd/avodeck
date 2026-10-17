"""`subject_key_hash` (core/crypto.py): the duplicate-case check compares keyed hashes, never identifiers.

An HMAC-SHA256 under a cell secret held by the key provider: the same input gives the same hash in one
cell, a different cell secret gives a different hash, the input cannot be recovered from a plain hash, and
when the key service cannot be reached processing pauses (fail closed, F24).
"""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from app.core import crypto
from app.core.crypto import LocalKeyProvider, subject_key_hash
from app.core.errors import ProcessingPaused

VALUE = "ZZ-ID-123456|1990-01-31"


def test_the_same_value_hashes_the_same_under_one_cell_secret() -> None:
    provider = LocalKeyProvider.generate()
    first = subject_key_hash(VALUE, provider=provider)
    assert first == subject_key_hash(VALUE, provider=provider)
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert first != subject_key_hash(VALUE + "x", provider=provider)


def test_another_cell_secret_gives_another_hash_and_it_is_not_a_plain_hash() -> None:
    a, b = LocalKeyProvider.generate(), LocalKeyProvider.generate()
    assert subject_key_hash(VALUE, provider=a) != subject_key_hash(VALUE, provider=b)
    assert subject_key_hash(VALUE, provider=a) != hashlib.sha256(VALUE.encode()).hexdigest()


def test_the_hash_is_not_any_other_use_of_the_provider_key() -> None:
    provider = LocalKeyProvider.generate()
    assert provider.mac(VALUE.encode(), purpose="subject_key") != provider.mac(VALUE.encode(), purpose="other")


def test_an_unreachable_key_service_pauses_processing() -> None:
    provider = LocalKeyProvider.generate()
    provider.reachable = False
    with pytest.raises(ProcessingPaused):
        subject_key_hash(VALUE, provider=provider)


def test_the_default_provider_is_the_cell_config(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(b"k" * 32).decode()
    monkeypatch.setenv("KEY_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_MASTER_KEY", key)
    crypto.cell_key_provider.cache_clear()
    try:
        assert subject_key_hash(VALUE) == subject_key_hash(VALUE, provider=LocalKeyProvider(b"k" * 32))
    finally:
        crypto.cell_key_provider.cache_clear()
