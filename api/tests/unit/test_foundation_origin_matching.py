"""Which Origin may send the API a request with cookies: the allow-list entry, or the same dev machine by another name.

Hosts are compared as parsed parts (scheme, host, port), not as text, so a look-alike never matches.
"""

from __future__ import annotations

import pytest

from app.core.http import origin_allowed

APP = ("https://app.example.test",)
DEV = ("http://localhost:5173",)


@pytest.mark.parametrize(
    ("origin", "allowed", "expected"),
    [
        ("https://app.example.test", APP, True),
        ("https://APP.example.test", APP, True),
        ("https://app.example.test/", APP, True),
        ("https://app.example.test:443", APP, True),
        ("http://app.example.test", APP, False),
        ("https://app.example.test.evil.test", APP, False),
        ("https://evil.test/https://app.example.test", APP, False),
        ("https://app.example.test:8443", APP, False),
        ("null", APP, False),
        ("", APP, False),
        ("http://localhost:5173", DEV, True),
        ("http://127.0.0.1:5173", DEV, True),
        ("http://[::1]:5173", DEV, True),
        ("http://localhost:5174", DEV, False),
        ("http://127.0.0.1:5173.evil.test", DEV, False),
        ("http://[::1]:5173", ("http://[::1]:5173",), True),
        ("http://localhost:5173", ("http://[::1]:5173",), True),
        ("http://[::1]:5174", ("http://[::1]:5173",), False),
        ("http://[::2]:5173", ("http://[::1]:5173",), False),
        ("https://localhost:5173", DEV, False),
        ("http://127.0.0.1.evil.test:5173", ("http://127.0.0.1:5173",), False),
    ],
)
def test_origin_allowed(origin: str, allowed: tuple[str, ...], expected: bool) -> None:
    assert origin_allowed(origin, allowed) is expected


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:pass@app.example.test",
        "https://app.example.test?x=1",
        "https://app.example.test#frag",
        "https://app.example.test/path",
    ],
)
def test_an_origin_with_more_than_scheme_host_and_port_is_refused(origin: str) -> None:
    assert origin_allowed(origin, APP) is False
