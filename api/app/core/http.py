"""Signed-in routes: session, CSRF, MFA, one database transaction per request, Idempotency-Key and If-Match.

Build every signed-in router with `router()`. Each request on it:
- refuses a browser `Origin` other than the web app's (403 `bad_origin`);
- resolves the device session from its cookie (401 `not_signed_in` or `session_expired`; a request
  counts as activity unless the router says `touch=False`);
- on a state-changing method, needs the CSRF token in `X-CSRF-Token` (403 `csrf_required`);
- needs MFA completed for this session (403 `mfa_required`) unless the router says `mfa_pending=True`;
- runs inside one `Database.request()` transaction as the restricted role, committed only if
  the response is a success (an error or an exception rolls everything back);
- with an `Idempotency-Key` on a state-changing method (F27): the first successful answer is
  kept for 24 hours and a repeat returns it with `Idempotent-Replayed: true`; the same key with
  a different request is 422 `idempotency_key_reused`. Keys belong to one person. A repeat runs the
  route's dependencies first (case access, step-up), so it is refused exactly as a first request
  would be; the kept answer is sealed at rest (`SecretBox`) and names its case.

Editable resources send `ETag: "<version>"`; updates take `IfMatch` (428 without it, 412 `stale`
when it is not the current version, F26).
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from psycopg import AsyncConnection

from app.core.auth import session as sessions
from app.core.auth.identity import Identity
from app.core.crypto import cell_secret_box
from app.core.db import RequestScope
from app.core.errors import ApiError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_KEPT_HEADERS = ("content-type", "etag", "location")


class _Discard(Exception):
    """Carries an error response out of the transaction so its work is rolled back."""

    def __init__(self, response: Response) -> None:
        self.response = response


async def _fingerprint(request: Request) -> str:
    digest = hashlib.sha256()
    for part in (request.method.encode(), request.url.path.encode(), request.url.query.encode(), await request.body()):
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def _seal_purpose() -> str:
    return "idempotency.response"


def _subject(person_id: uuid.UUID, key: str) -> str:
    return f"{person_id}:{key}"


async def _replay(conn: AsyncConnection, pending: _Pending) -> Response | None:
    # Serialise requests with the same key, so a double click waits for the first and replays it.
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(app.current_person_id()::text || ':' || %s, 0))",
        (pending.key,),
    )
    cur = await conn.execute(
        "select fingerprint, status_code, response_body, response_headers, case_id from app.idempotency_keys"
        " where person_id = app.current_person_id() and key = %s and expires_at > now()",
        (pending.key,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    stored_fingerprint, status, sealed, headers, case_id = row
    if stored_fingerprint != pending.fingerprint or case_id != pending.case_id:
        raise ApiError("idempotency_key_reused", "This Idempotency-Key was used for a different request.", 422)
    text = cell_secret_box().open(
        bytes(sealed).decode(), purpose=_seal_purpose(), subject=_subject(pending.person_id, pending.key)
    )
    body = base64.b64decode(text)
    return Response(content=body, status_code=status, headers={**headers, "idempotent-replayed": "true"})


async def _remember(conn: AsyncConnection, pending: _Pending, response: Response) -> None:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        return  # streamed responses are not kept
    headers = {h: response.headers[h] for h in _KEPT_HEADERS if h in response.headers}
    sealed = cell_secret_box().seal(
        base64.b64encode(body).decode(), purpose=_seal_purpose(), subject=_subject(pending.person_id, pending.key)
    )
    await conn.execute(
        """
        insert into app.idempotency_keys
          (person_id, key, fingerprint, status_code, response_body, response_headers, case_id)
        values (app.current_person_id(), %s, %s, %s, %s, %s, %s)
        on conflict (person_id, key) do update
          set fingerprint = excluded.fingerprint, status_code = excluded.status_code,
              response_body = excluded.response_body, response_headers = excluded.response_headers,
              case_id = excluded.case_id, created_at = now(), expires_at = now() + interval '24 hours'
        """,
        (pending.key, pending.fingerprint, response.status_code, sealed.encode(), json.dumps(headers), pending.case_id),
    )


@dataclasses.dataclass(frozen=True)
class _Pending:
    """An Idempotency-Key request waiting for its dependencies to pass before any replay."""

    conn: AsyncConnection
    person_id: uuid.UUID
    key: str
    fingerprint: str
    case_id: uuid.UUID | None


_PENDING: ContextVar[_Pending | None] = ContextVar("idempotency_pending", default=None)


def _case_id(request: Request) -> uuid.UUID | None:
    raw = request.path_params.get("case_id")
    try:
        return uuid.UUID(str(raw)) if raw is not None else None
    except ValueError:
        return None


def _replaying(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """The endpoint, preceded by the replay check: it runs after FastAPI has solved every dependency."""
    is_coroutine = inspect.iscoroutinefunction(endpoint)

    async def call(**values: Any) -> Any:
        pending = _PENDING.get()
        if pending is not None and (replayed := await _replay(pending.conn, pending)) is not None:
            return replayed
        if is_coroutine:
            return await endpoint(**values)
        return await run_in_threadpool(endpoint, **values)

    return call


def check_origin(request: Request) -> None:
    """A browser sends Origin on state-changing requests; only the web app's own is accepted."""
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != request.app.state.app_origin:
        raise ApiError("bad_origin", "Request refused.", 403)


def _no_store(response: Response) -> bool:
    return "no-store" in response.headers.get("cache-control", "")


class SessionRoute(APIRoute):
    """A signed-in route. `router()` makes subclasses with these two switches set."""

    mfa_pending = False  # True: also reachable before MFA is completed (the MFA routes themselves)
    touch = True  # False: checking the session does not count as activity

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        endpoint = self.dependant.call
        if endpoint is None or inspect.isgeneratorfunction(endpoint) or inspect.isasyncgenfunction(endpoint):
            handler = super().get_route_handler()
        else:
            plain = self.dependant
            self.dependant = dataclasses.replace(plain, call=_replaying(endpoint))
            try:
                handler = super().get_route_handler()
            finally:
                self.dependant = plain

        async def run(request: Request) -> Response:
            check_origin(request)
            database = request.app.state.database
            if database is None:
                raise ApiError("unavailable", "The service is not ready.", 503)
            session = await sessions.resolve(database, request.cookies.get(sessions.SESSION_COOKIE), touch=self.touch)
            if request.method not in SAFE_METHODS and not session.csrf_matches(
                request.headers.get(sessions.CSRF_HEADER)
            ):
                raise ApiError("csrf_required", "Reload the page and try again.", 403)
            if not self.mfa_pending and not session.mfa_complete:
                raise ApiError("mfa_required", "Enter a code from your authenticator app to continue.", 403)
            identity = Identity(session.person_id, session.email, session.full_name, session.mfa_enrolled)
            scope = RequestScope(
                person_id=identity.person_id,
                org_ids=(),
                request_id=request.headers.get("x-request-id") or uuid.uuid4().hex,
            )
            key = request.headers.get("idempotency-key") if request.method not in SAFE_METHODS else None
            fingerprint = await _fingerprint(request) if key else ""
            try:
                async with database.request(scope) as conn:
                    request.state.conn = conn
                    request.state.scope = scope  # for streamed bodies, which run after this transaction
                    request.state.identity = identity
                    request.state.session = session
                    pending = _Pending(conn, identity.person_id, key, fingerprint, _case_id(request)) if key else None
                    token = _PENDING.set(pending)
                    try:
                        response = await handler(request)
                    finally:
                        _PENDING.reset(token)
                    if response.status_code >= 400:
                        raise _Discard(response)
                    if pending is not None and response.headers.get("idempotent-replayed") is None:
                        if not _no_store(response):
                            await _remember(conn, pending, response)
                    return response
            except _Discard as discarded:
                return discarded.response

        return run


FoundationRoute = SessionRoute  # the step 3 name


class PublicRoute(APIRoute):
    """A route used before anyone is signed in (sign-in, recovery, password reset, email links).

    Only the Origin check runs here; each handler opens its own transactions. The CSRF guard
    (tests/guards/test_foundation_csrf.py) names every such route.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            check_origin(request)
            return await handler(request)

        return run


def router(*, mfa_pending: bool = False, touch: bool = True, **kwargs: Any) -> APIRouter:
    """An APIRouter whose routes run signed in (session, CSRF, MFA), in one transaction, with Idempotency-Key."""
    route_class: type[SessionRoute] = SessionRoute
    if mfa_pending or not touch:
        route_class = cast(
            "type[SessionRoute]", type("SessionRoute", (SessionRoute,), {"mfa_pending": mfa_pending, "touch": touch})
        )
    return APIRouter(route_class=route_class, **kwargs)


def public_router(**kwargs: Any) -> APIRouter:
    """Only for the signed-out steps; adding a route here needs the foundation owner (CSRF guard)."""
    return APIRouter(route_class=PublicRoute, **kwargs)


def _connection(request: Request) -> AsyncConnection:
    conn = getattr(request.state, "conn", None)
    if conn is None:
        raise RuntimeError("DbConn is only available on routes built with app.core.http.router()")
    return conn  # type: ignore[no-any-return]


def _identity(request: Request) -> Identity:
    identity: Identity = request.state.identity
    return identity


def _scope(request: Request) -> RequestScope:
    scope: RequestScope = request.state.scope
    return scope


DbConn = Annotated[AsyncConnection, Depends(_connection)]
CurrentScope = Annotated[RequestScope, Depends(_scope)]
"""Who is acting. A streamed response opens its own `database.request(scope)` per read, since the
request's transaction has ended by the time the body is sent."""
CurrentIdentity = Annotated[Identity, Depends(_identity)]
CurrentSession = Annotated[sessions.Session, Depends(sessions.current_session)]


# --- ETag and If-Match (F26) -----------------------------------------------------------------------

_ETAG = re.compile(r'(?:W/)?"(\d+)"')


def etag(version: int) -> str:
    return f'"{version}"'


def _if_match(if_match: Annotated[str | None, Header()] = None) -> int:
    if not if_match:
        raise ApiError("precondition_required", "Send If-Match with the ETag you last read.", 428)
    match = _ETAG.fullmatch(if_match.strip())
    if match is None:
        raise stale()
    return int(match.group(1))


def stale() -> ApiError:
    return ApiError("stale", "Someone else changed this first. Reload to see the latest.", 412)


IfMatch = Annotated[int, Depends(_if_match)]
