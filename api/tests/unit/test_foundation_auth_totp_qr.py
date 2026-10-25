"""Supabase Auth's REST API returns the authenticator QR code as bare SVG markup (its JavaScript
library adds the image prefix). core/auth/supabase.py hands the browser an image address it can show."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.core.auth.supabase import SupabaseAuth
from app.core.config import auth_settings

pytestmark = pytest.mark.anyio

ENV = {
    "SUPABASE_URL": "http://auth.example",
    "SUPABASE_ANON_KEY": "anon-k3y",
    "SUPABASE_SERVICE_ROLE_KEY": "service-k3y",
    "APP_ORIGIN": "https://app.example",
}
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="#000000" d="M0 0h1v1H0z"/></svg>'


def factors_transport(qr_code: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"factors": []})
        assert (request.method, request.url.path) == ("POST", "/auth/v1/factors")
        totp = {"qr_code": qr_code, "secret": "SECRET", "uri": "otpauth://totp/x"}
        return httpx.Response(200, json={"id": "factor-1", "totp": totp})

    return httpx.MockTransport(handler)


async def test_bare_svg_becomes_an_image_address() -> None:
    auth = SupabaseAuth(auth_settings(ENV), transport=factors_transport(SVG))
    qr = (await auth.enroll_totp("access"))["qr_code"]
    prefix = "data:image/svg+xml;base64,"
    assert qr.startswith(prefix)
    assert base64.b64decode(qr[len(prefix) :]).decode() == SVG


async def test_an_image_address_is_kept() -> None:
    ready = "data:image/svg+xml;base64," + base64.b64encode(SVG.encode()).decode()
    auth = SupabaseAuth(auth_settings(ENV), transport=factors_transport(ready))
    assert (await auth.enroll_totp("access"))["qr_code"] == ready
