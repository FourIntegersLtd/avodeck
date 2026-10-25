"""Temporary passwords support gives a new person (ADR 0024): long, made from characters that cannot be
mistaken for each other when read aloud, different every time, and sent to Supabase Auth with the login."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.auth.supabase import SupabaseAuth
from app.core.config import auth_settings
from app.features.organisations.accounts import ALPHABET, temporary_password

pytestmark = pytest.mark.anyio

ENV = {
    "SUPABASE_URL": "http://auth.example",
    "SUPABASE_ANON_KEY": "anon-k3y",
    "SUPABASE_SERVICE_ROLE_KEY": "service-k3y",
    "APP_ORIGIN": "https://app.example",
}


def test_a_temporary_password_is_long_plain_and_new_each_time() -> None:
    made = {temporary_password() for _ in range(200)}
    assert len(made) == 200
    assert not set("O0Il1") & set(ALPHABET)
    for password in made:
        letters = password.replace("-", "")
        assert len(letters) >= 16 and set(letters) <= set(ALPHABET)
        assert any(c.isupper() for c in letters) and any(c.islower() for c in letters)
        assert any(c.isdigit() for c in letters)


async def test_the_login_is_made_with_the_temporary_password() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "6f2c3a52-3e1f-4b8e-9d7a-0c1b2d3e4f50"})

    auth = SupabaseAuth(auth_settings(ENV), transport=httpx.MockTransport(handler))
    await auth.admin_create_user("a@example.test", password="Abcd-efgh-2345-JKLM-npqr")
    assert sent == [{"email": "a@example.test", "password": "Abcd-efgh-2345-JKLM-npqr", "email_confirm": True}]
