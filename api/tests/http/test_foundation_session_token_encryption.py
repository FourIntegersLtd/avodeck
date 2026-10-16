"""Supabase Auth tokens kept on the device session are sealed with `core/crypto.py` (architecture 9).

A database dump or a stray `select` never yields a usable Supabase token; the database itself
refuses a plaintext token; and everything that needs the token (refresh, sign-out) still works.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import psycopg
import pytest

from tests.support.auth import AuthAdmin, sign_in_with_mfa
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_auth_tokens_are_stored_sealed_and_sign_out_still_works(
    browser: Callable[[], Awaitable[httpx.AsyncClient]], auth_admin: AuthAdmin, admin: psycopg.Connection
) -> None:
    client = await browser()
    # Enrolling and verifying MFA call Supabase Auth with the stored access token, so this also
    # proves the sealed token opens again.
    signed_in = await sign_in_with_mfa(client, auth_admin)
    rows = admin.execute(
        "select auth_access_token, auth_refresh_token from app.sessions where person_id = %s",
        (signed_in.person_id,),
    ).fetchall()
    assert rows
    for access, refresh in rows:
        for token in (access, refresh):
            if token is None:
                continue
            # Booleans first, so a failure never prints the token.
            sealed, readable_jwt = token.startswith("av1:"), "eyJ" in token
            assert sealed, "a token is stored unsealed"
            assert not readable_jwt, "a JWT is stored readable"

    resp = await client.post("/v1/auth/sign-out")
    assert resp.status_code == 204, resp.text
    assert auth_admin.user(signed_in.person_id), "the account is untouched"


def test_the_database_refuses_a_plaintext_token(admin: psycopg.Connection, seed: Seed) -> None:
    session = seed.session(seed.person())
    for column in ("auth_access_token", "auth_refresh_token"):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(f"update app.sessions set {column} = 'eyJplain.token.value' where id = %s", (session.id,))
