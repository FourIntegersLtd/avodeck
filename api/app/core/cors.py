"""CORS for the web app's own origins, and nothing else (ADR 0031).

The browser reaches this API on its own host (`api.avodeck.com`) rather than through a rewrite on the
web app's origin, because a proxied request is cut off at 120 s on every Vercel plan and carries no
WebSockets. Every credentialed request is therefore cross-origin, and a browser only sends one, and
only lets the page read the answer, when the API says so.

What the API says is deliberately narrow:

- the allowed origin is reflected back by name, never `*`: a browser ignores `*` on a request with
  credentials, so a wildcard would fail silently as well as being wrong;
- `Vary: Origin` goes on every answer, allowed or not, so no shared cache can hand one origin's
  answer to another;
- a preflight is answered here, before any session, CSRF, transaction or database work. A preflight
  carries no cookies by definition, so it cannot be authenticated, and answering it early also keeps
  it off the connection pool;
- the header lists are written out in full. A header the client sends but this list omits makes the
  browser drop the request before the API ever sees it, and a response header missing from
  `Expose-Headers` is invisible to the page even though it arrived.

This does not replace the Origin check in `core/http.py`: CORS is what a browser enforces, and the
Origin check is what the server enforces. Both run.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.http import allowed_origins, origin_allowed

ALLOW_METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
# Every header the generated client and the chunked upload send (web/src/foundation/platform-client,
# web/src/foundation/lib/chunkedUpload.ts). Adding a request header to the frontend means adding it here.
ALLOW_HEADERS = "content-type, x-csrf-token, idempotency-key, if-match, x-chunk-sha256, x-request-id"
# Every response header the page reads: the ETag it sends back as If-Match, and whether an
# Idempotency-Key answer was a replay.
EXPOSE_HEADERS = "etag, idempotent-replayed"
# Chrome caps the preflight cache at 7200 s and Safari at around 600 s, so a chunk upload re-preflights
# now and then whatever we ask for. It is one small request against a saved 8 MiB one.
MAX_AGE = "7200"


class Cors:
    """Adds the CORS headers, and answers a preflight without touching anything else."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        allowed = origin is not None and origin_allowed(origin, allowed_origins(scope.get("app")))
        if scope["method"] == "OPTIONS" and headers.get("access-control-request-method"):
            await self._preflight(allowed, origin, send)
            return

        async def with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out.append("vary", "origin")
                if allowed and origin is not None:
                    out["access-control-allow-origin"] = origin
                    out["access-control-allow-credentials"] = "true"
                    out["access-control-expose-headers"] = EXPOSE_HEADERS
            await send(message)

        await self.app(scope, receive, with_cors)

    async def _preflight(self, allowed: bool, origin: str | None, send: Send) -> None:
        if not allowed or origin is None:
            body = b'{"error":{"code":"bad_origin","message":"Request refused."}}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"vary", b"origin"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", origin.encode()),
                    (b"access-control-allow-credentials", b"true"),
                    (b"access-control-allow-methods", ALLOW_METHODS.encode()),
                    (b"access-control-allow-headers", ALLOW_HEADERS.encode()),
                    (b"access-control-max-age", MAX_AGE.encode()),
                    (b"vary", b"origin"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})
