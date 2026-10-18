"""Supabase Auth, called only by FastAPI (rule 4; ADR 0013 allows the service-role key here only).

Supabase Auth's tokens never reach the browser: FastAPI keeps them on the session row. Every access
token it receives is checked here against the cell's published keys (ES256), audience, issuer,
expiry, subject and assurance level. Nothing in this module logs a request body, an address,
a password, a code or a token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm

from app.core.config import AuthSettings
from app.core.errors import ApiError

AUDIENCE = "authenticated"
ALGORITHMS = ["ES256"]
AAL_LEVELS = frozenset({"aal1", "aal2"})


class AuthError(ApiError):
    """A refusal from Supabase Auth, as the API error the browser sees."""


class InvalidToken(Exception):
    """An access token failed verification."""


def _unavailable() -> AuthError:
    return AuthError("auth_unavailable", "Signing in is not available just now. Try again shortly.", 503)


@dataclass(frozen=True)
class AccessClaims:
    person_id: uuid.UUID
    aal: str
    session_id: uuid.UUID | None


@dataclass(frozen=True)
class AuthTokens:
    person_id: uuid.UUID
    email: str = field(repr=False)
    aal: str
    session_id: uuid.UUID | None
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    factors: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    @property
    def mfa_enrolled(self) -> bool:
        return any(f.get("factor_type") == "totp" and f.get("status") == "verified" for f in self.factors)


class SupabaseAuth:
    def __init__(self, settings: AuthSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._issuer = f"{settings.url}/auth/v1"
        self._keys: dict[str, Any] = {}

    # --- transport ----------------------------------------------------------------------------

    async def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        admin: bool = False,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        key = self.settings.service_role_key if admin else self.settings.anon_key
        headers = {"apikey": key, "authorization": f"Bearer {token or key}"}
        try:
            async with httpx.AsyncClient(base_url=self._issuer, transport=self._transport, timeout=10.0) as client:
                return await client.request(method, path, headers=headers, json=json, params=params)
        except httpx.HTTPError as exc:
            raise _unavailable() from exc

    @staticmethod
    def _check(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code == 429:
            raise AuthError("too_many_attempts", "Too many attempts. Wait a few minutes and try again.", 429)
        if resp.status_code >= 400:
            raise _unavailable()
        body: dict[str, Any] = resp.json() if resp.content else {}
        return body

    async def _tokens(self, body: dict[str, Any]) -> AuthTokens:
        try:
            claims = await self.verify_access_token(body["access_token"])
        except (KeyError, InvalidToken) as exc:
            raise _unavailable() from exc
        user = body.get("user") or {}
        return AuthTokens(
            person_id=claims.person_id,
            email=str(user.get("email", "")).lower(),
            aal=claims.aal,
            session_id=claims.session_id,
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            expires_at=datetime.fromtimestamp(int(body.get("expires_at", 0)), UTC),
            factors=tuple(user.get("factors") or ()),
        )

    # --- signing in -----------------------------------------------------------------------------

    async def sign_in_with_password(self, email: str, password: str) -> AuthTokens:
        resp = await self._call(
            "POST", "/token", params={"grant_type": "password"}, json={"email": email, "password": password}
        )
        if resp.status_code in (400, 401, 422):
            raise AuthError("invalid_credentials", "That email and password do not match.", 401)
        return await self._tokens(self._check(resp))

    async def refresh(self, refresh_token: str) -> AuthTokens:
        resp = await self._call(
            "POST", "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token}
        )
        if resp.status_code in (400, 401, 403):
            raise AuthError("session_expired", "Your session has ended. Please sign in again.", 401)
        return await self._tokens(self._check(resp))

    async def sign_out(self, access_token: str, scope: str = "local") -> None:
        """Best effort: the session row is what FastAPI trusts, and it is revoked first."""
        try:
            await self._call("POST", "/logout", token=access_token, params={"scope": scope})
        except AuthError:
            pass

    # --- MFA --------------------------------------------------------------------------------------

    async def enroll_totp(self, access_token: str) -> dict[str, str]:
        # A setup left half-done blocks a new one (Supabase refuses a second unnamed factor): clear it.
        for factor in await self.factors(access_token):
            if factor.get("status") == "unverified":
                self._check(await self._call("DELETE", f"/factors/{factor['id']}", token=access_token))
        body = self._check(await self._call("POST", "/factors", token=access_token, json={"factor_type": "totp"}))
        totp = body.get("totp") or {}
        return {"factor_id": str(body["id"]), "qr_code": totp["qr_code"], "secret": totp["secret"], "uri": totp["uri"]}

    async def factors(self, access_token: str) -> list[dict[str, Any]]:
        body = self._check(await self._call("GET", "/user", token=access_token))
        return [f for f in body.get("factors") or [] if f.get("factor_type") == "totp"]

    async def verify_totp(self, access_token: str, factor_id: str, code: str) -> AuthTokens:
        challenge = self._check(await self._call("POST", f"/factors/{factor_id}/challenge", token=access_token))
        resp = await self._call(
            "POST",
            f"/factors/{factor_id}/verify",
            token=access_token,
            json={"challenge_id": challenge["id"], "code": code},
        )
        if resp.status_code in (400, 422):
            raise AuthError("invalid_code", "That code did not work. Check your authenticator app and try again.", 400)
        return await self._tokens(self._check(resp))

    # --- password reset -----------------------------------------------------------------------

    async def send_password_reset(self, email: str) -> None:
        """Supabase Auth sends the reset email (its template links to the web app, never to Auth)."""
        await self._call("POST", "/recover", json={"email": email})

    async def verify_recovery(self, token_hash: str) -> AuthTokens:
        resp = await self._call("POST", "/verify", json={"type": "recovery", "token_hash": token_hash})
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            raise AuthError("invalid_token", "That link has expired or was already used.", 400)
        return await self._tokens(self._check(resp))

    # --- admin (service-role key, Auth only) --------------------------------------------------

    async def admin_set_password(self, person_id: uuid.UUID, password: str) -> None:
        resp = await self._call("PUT", f"/admin/users/{person_id}", admin=True, json={"password": password})
        if resp.status_code in (400, 422):
            raise AuthError("weak_password", "Choose a longer password that is harder to guess.", 422)
        self._check(resp)

    async def admin_set_email(self, person_id: uuid.UUID, email: str) -> None:
        resp = await self._call(
            "PUT", f"/admin/users/{person_id}", admin=True, json={"email": email, "email_confirm": True}
        )
        if 400 <= resp.status_code < 500:
            raise AuthError("email_unavailable", "That email address cannot be used.", 409)
        self._check(resp)

    async def admin_delete_totp_factors(self, person_id: uuid.UUID) -> None:
        resp = await self._call("GET", f"/admin/users/{person_id}/factors", admin=True)
        listed: Any = resp.json() if resp.status_code < 400 else self._check(resp)
        for factor in listed:
            if factor.get("factor_type") == "totp":
                self._check(await self._call("DELETE", f"/admin/users/{person_id}/factors/{factor['id']}", admin=True))

    # --- access tokens ----------------------------------------------------------------------------

    async def _load_keys(self) -> None:
        body = self._check(await self._call("GET", "/.well-known/jwks.json"))
        keys: dict[str, Any] = {}
        for jwk in body.get("keys", []):
            if jwk.get("kty") == "EC" and jwk.get("kid"):
                keys[jwk["kid"]] = ECAlgorithm.from_jwk(jwk)
        self._keys = keys

    async def _key(self, kid: str) -> Any:
        if kid not in self._keys:
            await self._load_keys()  # unknown key id: the cell may have rotated its keys
        if kid not in self._keys:
            raise InvalidToken("unknown signing key")
        return self._keys[kid]

    async def verify_access_token(self, token: str) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken("malformed") from exc
        if header.get("alg") not in ALGORITHMS or not header.get("kid"):
            raise InvalidToken("algorithm or key id not allowed")
        key = await self._key(str(header["kid"]))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=AUDIENCE,
                issuer=self._issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
            person_id = uuid.UUID(str(claims["sub"]))
            session_id = uuid.UUID(str(claims["session_id"])) if claims.get("session_id") else None
        except (jwt.PyJWTError, ValueError) as exc:
            raise InvalidToken("refused") from exc
        aal = claims.get("aal")
        if aal not in AAL_LEVELS:
            raise InvalidToken("no assurance level")
        return AccessClaims(person_id=person_id, aal=str(aal), session_id=session_id)
