# ADR 0031: the browser talks straight to the API, on its own origin

Date: 2026-10-31 · Status: accepted · Supersedes the "same-origin only" rule in `web/AGENTS.md`

## Context

`avodeck.com` is served by Vercel and `web/vercel.json` rewrote `/v1/*` to `https://api.avodeck.com`
(Railway, Amsterdam). `VITE_API_ORIGIN` was empty, so the page called its own origin and **every** API
call, **every** 8 MiB chunk `PUT` and the job event stream went through the Vercel edge.

Vercel cuts a proxied request at **120 seconds** on every plan — Hobby, Pro and Enterprise alike — and
nothing documents it as raisable; a request that outlives it gets `ROUTER_EXTERNAL_TARGET_ERROR`
(measured 2026-10-31, `docs/runbooks/railway.md`). Body size was not the problem: a 9 MiB `PUT` passed
through intact. Time was. An 8 MiB chunk had to finish in 120 s, which needs about 560 kbit/s sustained
upstream; below that every chunk times out and so does the retry. A rural expert uploading a 4,000-page
bundle could not get it in.

Two more things were blocked by the same cause. Vercel rewrites do not carry WebSockets, so live
dictation (S14/S15, Azure Speech relayed through FastAPI) could not work in the cloud at all. And every
byte of patient data passed through Vercel's edge, which rule 5 would rather it did not.

## Decision

The browser calls `https://api.avodeck.com` directly. Vercel serves static files only: the `/v1` rewrite
is deleted and `VITE_API_ORIGIN=https://api.avodeck.com` is set on the Vercel project.

Railway's own limits then apply: 15 minutes active, 5 minutes idle, a request body that must finish
inside 5 minutes, and no documented body-size limit. The required sustained upstream for one chunk falls
from about 560 kbit/s to about 225 kbit/s, so `CHUNK_SIZE` stays at 8 MiB.

What this costs, and how each part is paid:

- **CORS.** `api/app/core/cors.py`, a pure-ASGI middleware added outermost in `app/main.py` so a
  preflight is answered before the session, CSRF and transaction machinery and so SSE is not buffered.
  It reflects an `Origin` only when it is on the allow-list, always sends `Vary: Origin`, allows
  credentials, and exposes `ETag` and `Idempotent-Replayed` (a cross-origin response hides them). It is
  not FastAPI's `CORSMiddleware`, so `APP_ORIGIN` stays the single source of truth.
- **`APP_ORIGIN` is an allow-list** (comma-separated, one entry in production). A wildcard is refused at
  start-up: a browser ignores `*` on a credentialed request, so it would fail silently.
- **The origin check stays.** CORS is what the browser enforces; `check_origin` is what the server
  enforces. It now also refuses a change that carries **no** `Origin` on a cookie-signed route — the API
  has its own origin, so a browser always sends one. A route a machine calls with its own proof (the
  signed e-sign webhook) opts out with `public_router(machine=True)`.
- **Cookies.** `avodeck.com` and `api.avodeck.com` share a registrable domain, so they are *same-site*
  though cross-origin: a `SameSite=Strict` cookie is still sent. `av_session` therefore stays host-only
  and `Strict`. `av_csrf` gains `Domain` from a new `COOKIE_DOMAIN` setting (empty by default, so local
  dev and tests stay host-only) because the page has to read it for the double submit. Holding the token
  in JS memory instead is ruled out by `web/AGENTS.md`.
- **CSP.** `img-src` gains the API origin (the signature picture on S18 comes straight from FastAPI).
  `connect-src` already had it and its `ws` form. No other host may be named, which the guards pin.
- **Heartbeats.** Railway closes an idle request at 5 minutes and community reports put a cutoff near a
  minute on a request that sends nothing, so the job stream emits a `: keepalive` comment every 20 s and
  ends itself at 240 s, under the idle close, for a clean `EventSource` reconnect.

## Alternatives rejected

| Option | What the limit becomes | Why not |
|---|---|---|
| Ask Vercel to raise 120 s | Listed flat on every plan, not marked `Custom` | Nothing documents it as raisable |
| Keep Vercel, add heartbeats | Unverified; one report says rewrites buffer | Cannot be the production answer |
| Keep Vercel, smaller chunks | Still 120 s, now per chunk | Hides it; does nothing for the stream or dictation |
| Cloudflare proxying (orange cloud) | 125 s proxy read, 30 s proxy **write**, repeated SSE buffering | Worse than today; keep the grey cloud |

## Consequences

- Vercel leaves the patient-data path entirely: one fewer processor to list and to trust (rule 5).
- Live dictation becomes possible in the cloud. A WebSocket handshake is not subject to CORS, so the
  socket's own origin check (`core/auth/access.py`) is the only thing guarding it, and it now uses the
  same allow-list.
- Safari re-runs a preflight roughly every 10 minutes whatever `Access-Control-Max-Age` says, so each
  chunk `PUT` may cost one extra small request. Accepted.
- `VITE_API_ORIGIN` is baked in at build time. An empty value now produces a build that calls a dead
  `/v1` path, so it is a required Vercel setting, written in the runbook.
- Rollback: revert the `vercel.json` and `VITE_API_ORIGIN` change and redeploy the web app. CORS, the
  cookie domain and the heartbeats all work unchanged on the same-origin setup, so they stay.
