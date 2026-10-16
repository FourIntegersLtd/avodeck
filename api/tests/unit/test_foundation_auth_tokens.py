"""core/auth/supabase.py checks every Supabase Auth access token itself: signature against the
cell's published keys (ES256 only), audience, issuer, expiry, subject and assurance level.
core/config.py reads the Auth settings and never shows the keys."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.core.auth.supabase import InvalidToken, SupabaseAuth
from app.core.config import ConfigError, auth_settings

pytestmark = pytest.mark.anyio

URL = "http://auth.example"
ENV = {
    "SUPABASE_URL": URL,
    "SUPABASE_ANON_KEY": "anon-k3y",
    "SUPABASE_SERVICE_ROLE_KEY": "service-k3y",
    "APP_ORIGIN": "https://app.example",
}
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
KID = "kid-1"


def jwks_transport(calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/auth/v1/.well-known/jwks.json"
        jwk = json.loads(ECAlgorithm.to_jwk(KEY.public_key()))
        return httpx.Response(200, json={"keys": [{**jwk, "kid": KID, "alg": "ES256", "use": "sig"}]})

    return httpx.MockTransport(handler)


def claims(**over: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": f"{URL}/auth/v1",
        "exp": now + 300,
        "iat": now,
        "aal": "aal2",
        "session_id": str(uuid.uuid4()),
        "email": "someone@example.test",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def sign(payload: dict[str, Any], key: Any = KEY, kid: str = KID, alg: str = "ES256") -> str:
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def auth(calls: list[str]) -> SupabaseAuth:
    return SupabaseAuth(auth_settings(ENV), transport=jwks_transport(calls))


async def test_a_valid_token_gives_the_person_and_assurance_level(auth: SupabaseAuth, calls: list[str]) -> None:
    payload = claims()
    verified = await auth.verify_access_token(sign(payload))
    assert verified.person_id == uuid.UUID(payload["sub"])
    assert verified.aal == "aal2"
    assert verified.session_id == uuid.UUID(payload["session_id"])
    await auth.verify_access_token(sign(claims(aal="aal1")))
    assert len(calls) == 1, "the published keys are cached"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: sign(claims(aud="anon")), id="wrong-audience"),
        pytest.param(lambda: sign(claims(iss="http://other.example/auth/v1")), id="wrong-issuer"),
        pytest.param(lambda: sign(claims(exp=int(time.time()) - 60)), id="expired"),
        pytest.param(lambda: sign(claims(), key=OTHER_KEY), id="other-key"),
        pytest.param(lambda: sign(claims(aal=None)), id="no-aal"),
        pytest.param(lambda: sign(claims(aal="aal9")), id="unknown-aal"),
        pytest.param(lambda: sign(claims(sub=None)), id="no-subject"),
        pytest.param(lambda: sign(claims(), key="a-shared-secret-of-enough-length!!", alg="HS256"), id="hs256"),
        pytest.param(lambda: jwt.encode(claims(), "", algorithm="none"), id="alg-none"),
        pytest.param(lambda: "not-a-token", id="garbage"),
    ],
)
async def test_bad_tokens_are_refused(auth: SupabaseAuth, token: Any) -> None:
    with pytest.raises(InvalidToken):
        await auth.verify_access_token(token())


async def test_an_unknown_key_id_refetches_the_keys_once(auth: SupabaseAuth, calls: list[str]) -> None:
    await auth.verify_access_token(sign(claims()))
    with pytest.raises(InvalidToken):
        await auth.verify_access_token(sign(claims(), kid="rotated"))
    assert len(calls) == 2


def test_auth_settings_come_from_the_environment() -> None:
    settings = auth_settings(ENV)
    assert settings.url == URL
    assert settings.app_origin == "https://app.example"


@pytest.mark.parametrize("missing", sorted(ENV))
def test_missing_auth_settings_are_a_clear_error(missing: str) -> None:
    with pytest.raises(ConfigError, match=missing):
        auth_settings({k: v for k, v in ENV.items() if k != missing})


def test_auth_keys_never_show_when_printed_or_logged() -> None:
    text = repr(auth_settings(ENV)) + str(auth_settings(ENV))
    assert "anon-k3y" not in text and "service-k3y" not in text
