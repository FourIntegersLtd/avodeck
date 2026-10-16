"""Signed-in routes: one database transaction per request, Idempotency-Key and If-Match.

Build every signed-in router with `router()`. Each request on it:
- resolves the identity (401 `not_signed_in` without one);
- runs inside one `Database.request()` transaction as the restricted role, committed only if
  the response is a success (an error or an exception rolls everything back);
- with an `Idempotency-Key` on a state-changing method (F27): the first successful answer is
  kept for 24 hours and a repeat returns it with `Idempotent-Replayed: true`; the same key with
  a different request is 422 `idempotency_key_reused`. Keys belong to one person.

Editable resources send `ETag: "<version>"`; updates take `IfMatch` (428 without it, 412 `stale`
when it is not the current version, F26).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.routing import APIRoute
from psycopg import AsyncConnection

from app.core.auth.identity import Identity
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


async def _replay(conn: AsyncConnection, key: str, fingerprint: str) -> Response | None:
    # Serialise requests with the same key, so a double click waits for the first and replays it.
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(app.current_person_id()::text || ':' || %s, 0))", (key,)
    )
    cur = await conn.execute(
        "select fingerprint, status_code, response_body, response_headers from app.idempotency_keys"
        " where person_id = app.current_person_id() and key = %s and expires_at > now()",
        (key,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    stored_fingerprint, status, body, headers = row
    if stored_fingerprint != fingerprint:
        raise ApiError("idempotency_key_reused", "This Idempotency-Key was used for a different request.", 422)
    return Response(content=bytes(body), status_code=status, headers={**headers, "idempotent-replayed": "true"})


async def _remember(conn: AsyncConnection, key: str, fingerprint: str, response: Response) -> None:
    body = getattr(response, "body", None)
    if not isinstance(body, bytes):
        return  # streamed responses are not kept
    headers = {h: response.headers[h] for h in _KEPT_HEADERS if h in response.headers}
    await conn.execute(
        """
        insert into app.idempotency_keys (person_id, key, fingerprint, status_code, response_body, response_headers)
        values (app.current_person_id(), %s, %s, %s, %s, %s)
        on conflict (person_id, key) do update
          set fingerprint = excluded.fingerprint, status_code = excluded.status_code,
              response_body = excluded.response_body, response_headers = excluded.response_headers,
              created_at = now(), expires_at = now() + interval '24 hours'
        """,
        (key, fingerprint, response.status_code, body, json.dumps(headers)),
    )


class FoundationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            provider = request.app.state.identity_provider
            identity: Identity | None = await provider(request) if provider is not None else None
            if identity is None:
                raise ApiError("not_signed_in", "Please sign in.", 401)
            database = request.app.state.database
            if database is None:
                raise ApiError("unavailable", "The service is not ready.", 503)
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
                    request.state.identity = identity
                    if key and (replayed := await _replay(conn, key, fingerprint)) is not None:
                        return replayed
                    response = await handler(request)
                    if response.status_code >= 400:
                        raise _Discard(response)
                    if key:
                        await _remember(conn, key, fingerprint, response)
                    return response
            except _Discard as discarded:
                return discarded.response

        return run


def router(**kwargs: Any) -> APIRouter:
    """An APIRouter whose routes run signed in, in one transaction, with Idempotency-Key support."""
    return APIRouter(route_class=FoundationRoute, **kwargs)


def _connection(request: Request) -> AsyncConnection:
    conn = getattr(request.state, "conn", None)
    if conn is None:
        raise RuntimeError("DbConn is only available on routes built with app.core.http.router()")
    return conn  # type: ignore[no-any-return]


def _identity(request: Request) -> Identity:
    identity: Identity = request.state.identity
    return identity


DbConn = Annotated[AsyncConnection, Depends(_connection)]
CurrentIdentity = Annotated[Identity, Depends(_identity)]


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
