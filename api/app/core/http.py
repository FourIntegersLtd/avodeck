"""Signed-in routes: session, CSRF, MFA, one database transaction per request, Idempotency-Key and If-Match.

Build every signed-in router with `router()`. Each request on it:
- refuses an `Origin` other than the web app's, and a change with none at all (403 `bad_origin`);
- resolves the device session from its cookie (401 `not_signed_in` or `session_expired`; a request
  counts as activity unless the router says `touch=False`);
- on a state-changing method, needs the CSRF token in `X-CSRF-Token` (403 `csrf_required`);
- does not need MFA: sign-in is password only (ADR 0025); risky actions need step-up (rule 10,
  `core/auth/step_up.py`) and support routes need MFA this session (`core/auth/support.py`);
- refuses a person still on a temporary password from support (403 `password_change_required`, ADR 0024)
  unless the router says `mfa_pending=True` or `password_pending=True` (the guard
  tests/guards/test_foundation_password_change.py names every such route);
- runs inside one `Database.request()` transaction as the restricted role, committed only if
  the response is a success (an error or an exception rolls everything back);
- with an `Idempotency-Key` on a state-changing method (F27): the first successful answer is
  kept for 24 hours and a repeat returns it with `Idempotent-Replayed: true`; the same key with
  a different request is 422 `idempotency_key_reused`. Keys belong to one person. A repeat runs the
  route's dependencies first (case access, step-up), so it is refused exactly as a first request
  would be; the kept answer is sealed at rest (`SecretBox`) and names its case. A `Cache-Control: no-store`
  answer is not kept, unless the route names what a repeat gets instead (`keep_instead`): an answer
  carrying a one-time secret keeps a copy without it.

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
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.core import audit
from app.core.auth import session as sessions
from app.core.auth.identity import Identity
from app.core.crypto import cell_secret_box
from app.core.db import RequestScope
from app.core.errors import ApiError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_KEPT_HEADERS = ("content-type", "etag", "location", "cache-control")


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


async def _remember(conn: AsyncConnection, pending: _Pending, response: Response, body: bytes | None = None) -> None:
    body = body if body is not None else getattr(response, "body", None)
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


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_parts(origin: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) of an origin, host lowercased and the scheme's default port filled in."""
    try:
        parts = urlsplit(origin)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or host is None or parts.path not in ("", "/"):
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    return parts.scheme, host, port if port is not None else _DEFAULT_PORTS[parts.scheme]


def allowed_origins(app: Any) -> tuple[str, ...]:
    """The cell's `APP_ORIGIN` list: every origin allowed to send a request with cookies."""
    origins = getattr(getattr(app, "state", None), "app_origins", ())
    return tuple(origins or ())


def origin_matches(origin: str, allowed: str) -> bool:
    """Same scheme, host and port; localhost, 127.0.0.1 and [::1] are one dev machine."""
    got, want = _origin_parts(origin), _origin_parts(allowed)
    if got is None or want is None:
        return False
    if got == want:
        return True
    return got[0] == want[0] and got[2] == want[2] and got[1] in _LOOPBACK and want[1] in _LOOPBACK


def origin_allowed(origin: str, allowed: tuple[str, ...]) -> bool:
    return any(origin_matches(origin.rstrip("/"), candidate) for candidate in allowed)


def check_origin(request: Request, *, require: bool = False) -> None:
    """Only the web app's own origins may send this API a request; a browser always names itself.

    This runs whatever CORS says: CORS is what the browser enforces, this is what the server does.
    With `require`, a change arriving with no `Origin` at all is refused as well. That is for the
    routes a cookie signs in: the API is on its own origin now (ADR 0031), so a browser always sends
    one, and without it there is no way to tell where the request came from. Routes that carry their
    own proof instead of a cookie — a signed webhook, a health check — have no cookie to abuse, so a
    missing `Origin` is fine there; a foreign one is still refused.
    """
    origin = request.headers.get("origin")
    if origin is None:
        if require and request.method not in SAFE_METHODS:
            raise ApiError("bad_origin", "Request refused.", 403)
        return
    if not origin_allowed(origin, allowed_origins(request.app)):
        raise ApiError("bad_origin", "Request refused.", 403)


def _no_store(response: Response) -> bool:
    return "no-store" in response.headers.get("cache-control", "")


def keep_instead(request: Request, body: BaseModel) -> None:
    """For a `no-store` answer carrying a one-time secret: what a repeat with the same Idempotency-Key gets
    instead (the same answer without the secret). Never pass anything holding the secret."""
    request.state.idempotent_body = body.model_dump_json().encode()


class SessionRoute(APIRoute):
    """A signed-in route. `router()` makes subclasses with these two switches set."""

    mfa_pending = False  # True: the MFA routes themselves; also reachable on a temporary password (ADR 0024)
    password_pending = False  # True: also reachable while the person must change their password (ADR 0024)
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
            check_origin(request, require=True)
            database = request.app.state.database
            if database is None:
                raise ApiError("unavailable", "The service is not ready.", 503)
            token = request.cookies.get(sessions.SESSION_COOKIE)
            if not token:
                raise sessions.not_signed_in()
            request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
            key = request.headers.get("idempotency-key") if request.method not in SAFE_METHODS else None
            fingerprint = await _fingerprint(request) if key else ""
            scopes: list[RequestScope] = []  # filled once the session is known
            refusal: ApiError | None = None
            try:
                # One connection, one transaction: the session lookup (which also ends an idle or expired
                # session and counts activity) shares it with the route's own work.
                async with (
                    audit.detached(database, lambda: scopes[0] if scopes else None),
                    database.session_request(sessions.resolve_args(token, touch=self.touch), request_id) as (conn, row),
                ):
                    outcome = sessions.from_row(row)
                    if isinstance(outcome, ApiError):
                        refusal = outcome  # leaving normally commits the session's own writes (the revoke)
                    else:
                        session = outcome
                        if request.method not in SAFE_METHODS and not session.csrf_matches(
                            request.headers.get(sessions.CSRF_HEADER)
                        ):
                            refusal = ApiError("csrf_required", "Reload the page and try again.", 403)
                        elif session.must_change_password and not (self.mfa_pending or self.password_pending):
                            refusal = ApiError("password_change_required", "Choose your own password to continue.", 403)
                    if refusal is None:
                        identity = Identity(session.person_id, session.email, session.full_name, session.mfa_enrolled)
                        scopes.append(RequestScope(person_id=identity.person_id, org_ids=(), request_id=request_id))
                        request.state.conn = conn
                        request.state.scope = scopes[0]  # for streamed bodies, which run after this transaction
                        request.state.identity = identity
                        request.state.session = session
                        pending = (
                            _Pending(conn, identity.person_id, key, fingerprint, _case_id(request)) if key else None
                        )
                        pending_token = _PENDING.set(pending)
                        try:
                            response = await handler(request)
                        finally:
                            _PENDING.reset(pending_token)
                        if response.status_code >= 400:
                            raise _Discard(response)
                        if pending is not None and response.headers.get("idempotent-replayed") is None:
                            instead: bytes | None = getattr(request.state, "idempotent_body", None)
                            if instead is not None:
                                await _remember(conn, pending, response, body=instead)
                            elif not _no_store(response):
                                await _remember(conn, pending, response)
                        return response
            except _Discard as discarded:
                return discarded.response
            assert refusal is not None
            raise refusal

        return run


FoundationRoute = SessionRoute  # the step 3 name


class PublicRoute(APIRoute):
    """A route used before anyone is signed in (sign-in, recovery, password reset, email links).

    Only the Origin check runs here; each handler opens its own transactions. The CSRF guard
    (tests/guards/test_foundation_csrf.py) names every such route. `require_origin` is off only for
    the routes a machine calls (a signed webhook), which send no `Origin` at all.
    """

    require_origin = True

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            check_origin(request, require=self.require_origin)
            return await handler(request)

        return run


def router(
    *, mfa_pending: bool = False, password_pending: bool = False, touch: bool = True, **kwargs: Any
) -> APIRouter:
    """An APIRouter whose routes run signed in (session, CSRF, MFA), in one transaction, with Idempotency-Key."""
    route_class: type[SessionRoute] = SessionRoute
    if mfa_pending or password_pending or not touch:
        switches = {"mfa_pending": mfa_pending, "password_pending": password_pending, "touch": touch}
        route_class = cast("type[SessionRoute]", type("SessionRoute", (SessionRoute,), switches))
    return APIRouter(route_class=route_class, **kwargs)


def public_router(*, machine: bool = False, **kwargs: Any) -> APIRouter:
    """Only for the signed-out steps; adding a route here needs the foundation owner (CSRF guard).

    `machine=True` is for a caller that is not a browser and proves itself some other way (a signed
    webhook): it may send no `Origin`. A browser route must not use it — a change with no `Origin`
    there has nothing left saying where it came from.
    """
    route_class: type[PublicRoute] = PublicRoute
    if machine:
        route_class = cast("type[PublicRoute]", type("MachinePublicRoute", (PublicRoute,), {"require_origin": False}))
    return APIRouter(route_class=route_class, **kwargs)


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
