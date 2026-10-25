"""The Auth login support makes (migration 1012): only Supabase's "email exists" answer is a 409 conflict;
any other refusal of the email is 422 `invalid_email` ("check the email address"), and a fault is 503."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.auth.supabase import AuthError, SupabaseAuth
from app.core.config import auth_settings

pytestmark = pytest.mark.anyio

ENV = {
    "SUPABASE_URL": "http://auth.example",
    "SUPABASE_ANON_KEY": "anon-k3y",
    "SUPABASE_SERVICE_ROLE_KEY": "service-k3y",
    "APP_ORIGIN": "https://app.example",
}


def answering(status: int, body: dict[str, Any]) -> SupabaseAuth:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/admin/users"
        return httpx.Response(status, json=body)

    return SupabaseAuth(auth_settings(ENV), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (
            422,
            {
                "code": 422,
                "error_code": "email_exists",
                "msg": "A user with this email address has already been registered",
            },
        ),
        (422, {"error_code": "user_already_exists"}),
        (400, {"code": "email_exists"}),
    ],
)
async def test_an_email_that_already_has_a_login_is_a_conflict(status: int, body: dict[str, Any]) -> None:
    with pytest.raises(AuthError) as refused:
        await answering(status, body).admin_create_user("a@example.test")
    assert (refused.value.code, refused.value.status) == ("conflict", 409)


@pytest.mark.parametrize(
    ("status", "body"),
    [(400, {"error_code": "validation_failed"}), (422, {"error_code": "email_address_invalid"}), (422, {})],
)
async def test_any_other_refusal_of_the_email_asks_to_check_it(status: int, body: dict[str, Any]) -> None:
    with pytest.raises(AuthError) as refused:
        await answering(status, body).admin_create_user("a@example.test")
    assert (refused.value.code, refused.value.status) == ("invalid_email", 422)


async def test_a_fault_is_unavailable() -> None:
    with pytest.raises(AuthError) as refused:
        await answering(500, {}).admin_create_user("a@example.test")
    assert (refused.value.code, refused.value.status) == ("auth_unavailable", 503)
