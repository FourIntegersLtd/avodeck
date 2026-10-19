"""S28: "7 of 10 left" needs the count of unused backup codes, never the codes themselves."""

from __future__ import annotations

import psycopg
import pytest

from tests.http.test_foundation_auth_routes import Browser
from tests.support.auth import AuthAdmin, sign_in_with_mfa

pytestmark = pytest.mark.anyio


async def test_count_of_unused_backup_codes(browser: Browser, auth_admin: AuthAdmin, admin: psycopg.Connection) -> None:
    client = await browser()
    signed = await sign_in_with_mfa(client, auth_admin)
    resp = await client.get("/v1/auth/mfa/recovery-codes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"remaining": 10, "total": 10}
    assert "no-store" in resp.headers.get("cache-control", "")
    admin.execute(
        "update app.mfa_recovery_codes set used_at = now() where id in "
        "(select id from app.mfa_recovery_codes where person_id = %s limit 3)",
        (signed.person_id,),
    )
    admin.commit()
    assert (await client.get("/v1/auth/mfa/recovery-codes")).json() == {"remaining": 7, "total": 10}
    assert "recovery_codes" not in (await client.get("/v1/auth/mfa/recovery-codes")).json()


async def test_the_count_needs_a_signed_in_session(browser: Browser) -> None:
    assert (await (await browser()).get("/v1/auth/mfa/recovery-codes")).status_code == 401
