"""Malformed sign-in input is refused by the API (422 `invalid`), never passed on to Auth as a 503.

Control characters (a NUL byte, a line break) cannot be part of an email address, password, code or
link token. Found by Schemathesis: they used to reach Supabase Auth, whose refusal became 503
`auth_unavailable`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

pytestmark = pytest.mark.anyio

Browser = Callable[[], Awaitable[httpx.AsyncClient]]
BAD = ["a\x00b@example.test", "a@example.test\n", "a\x7f@example.test"]


@pytest.mark.parametrize("email", BAD)
async def test_sign_in_refuses_control_characters_in_the_email(browser: Browser, email: str) -> None:
    resp = await (await browser()).post("/v1/auth/sign-in", json={"email": email, "password": "Long-enough-pass-1"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid"


async def test_sign_in_refuses_control_characters_in_the_password(browser: Browser) -> None:
    resp = await (await browser()).post(
        "/v1/auth/sign-in", json={"email": "a@example.test", "password": "pass\x00word"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("path", ["/v1/auth/password/forgot"])
async def test_forgot_refuses_control_characters(browser: Browser, path: str) -> None:
    resp = await (await browser()).post(path, json={"email": "a\x00@example.test"})
    assert resp.status_code == 422


async def test_reset_refuses_control_characters_in_the_token_and_password(browser: Browser) -> None:
    client = await browser()
    bad_token = await client.post(
        "/v1/auth/password/reset", json={"token": "abc\x00", "new_password": "Long-enough-pass-1"}
    )
    bad_password = await client.post(
        "/v1/auth/password/reset", json={"token": "abc", "new_password": "Long-enough\x00pass-1"}
    )
    assert (bad_token.status_code, bad_password.status_code) == (422, 422)
