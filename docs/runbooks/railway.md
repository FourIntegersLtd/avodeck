# Railway: how the API and worker are deployed

ADR 0021. One Docker image (`api/Dockerfile`, built from the repository root), three services in one Railway project (`api`, `worker`, `cron`), region **europe-west4 (Amsterdam)**. There is no `railway.toml`. The same settings are written as config-as-code in `railway/api.json`, `railway/worker.json` and `railway/cron.json`; in the dashboard, set each service's config file path (Settings > Config-as-code) to its file, or copy the values below by hand.

| Setting | `api` | `worker` | `cron` |
|---|---|---|---|
| Source | this repo, branch `main` | same | same |
| Dockerfile path | `api/Dockerfile` | `api/Dockerfile` | `api/Dockerfile` |
| Start command | (image default) | `python -m app.worker.main` | `python -m app.worker.schedules` |
| Cron schedule | none | none | `15 2 * * *` (UTC, daily) |
| Health check path | `/v1/cell` | none | none |
| Public networking | yes (generate a domain) | no | no |
| Pre-deploy command | none | none | none |
| Volumes | none | none | none |
| Replicas | 1 | 1 | 1 (runs, then exits) |

The `cron` service runs each daily schedule once for today's date and exits (code 1 if any schedule failed; the others still run). A schedule already run for a date is skipped, so a retry or a second start is harmless. Its logs list schedule names and counts only.

## Variables

Copy every name from `api/.env.example` into both services (Railway "shared variables"), with cloud values. Never paste them into chat or commit them.

- `DATABASE_URL` and `DATABASE_URL_DIRECT`: the Supabase **session pooler**, not the direct host (IPv6 only):
  `postgresql://<role>.<project-ref>:<password>@aws-0-eu-west-2.pooler.supabase.com:5432/postgres?sslmode=require`
  with `app_api` for `DATABASE_URL` and `app_worker` for `DATABASE_URL_DIRECT`.
- `APP_ORIGIN`: the web app's public origins, comma-separated (`https://avodeck.com`). Never a wildcard; start-up refuses one.
- `COOKIE_DOMAIN`: `avodeck.com`, so the page on `avodeck.com` can read the `av_csrf` cookie the API sets (ADR 0031). Empty locally.
- `PACKS_APPROVED_ONLY=1` once the launch packs are approved.

## Migrations

Not run by Railway. From the founder's Mac, before deploying code that needs them:
`supabase db push --linked`

## Web app (Vercel)

The browser calls the API **directly** on `https://api.avodeck.com` (ADR 0031). Vercel serves static files
only and proxies nothing, so Vercel's 120 s limit on a proxied request no longer applies to anything.

Required on the Vercel project (Production, and any preview that should work):

```
VITE_API_ORIGIN=https://api.avodeck.com
```

It is baked into the bundle at build time by `cspPlugin` (`web/csp.ts`), so a change needs a redeploy, and
an empty value ships a build that calls a dead `/v1` path on the web host.

One Vercel project (`avodeck-site`, root directory `web`) serves `avodeck.com`: `web/scripts/add-landing.sh`
puts the landing page (`site/`) at `/` and `/privacy`, and the app's `index.html` becomes `app.html`, the
fallback for every other path. `web/vercel.json` holds no rewrite to the API.

The api service's public name is `api.avodeck.com` (Railway custom domain, target port 8000). In Cloudflare
it is a CNAME `api` to the value Railway shows, **DNS only (grey cloud)** — proxying it would put a 125 s
read and a 30 s write timeout back in the way, plus Cloudflare's history of buffering server-sent events.
The Railway name `api-production-0e15e.up.railway.app` still works.

**Rollback** (if a cross-origin problem appears in production): revert the `web/vercel.json` and
`VITE_API_ORIGIN` change and redeploy the web app. The API's CORS, cookie domain and stream heartbeats all
work unchanged on the same-origin setup, so they stay in place.

## Proxy limits

Measured 2026-10-31, when every call still went through the Vercel rewrite — the reason it was removed:

- **Body size: no cap at 9 MiB.** A 9 MiB `PUT` (9437184 bytes) reached FastAPI intact through
  `avodeck.com` and was answered 401 `not_signed_in` (about 13 s on a domestic connection). The 4.5 MB
  body limit in Vercel's docs applies to Vercel Functions, not to a rewrite to an external destination.
- **Proxied request timeout: 120 s on every plan** (`https://vercel.com/docs/limits`; not raisable). A
  request not finished by then gets `ROUTER_EXTERNAL_TARGET_ERROR`. An 8 MiB chunk therefore needed about
  560 kbit/s sustained upstream, which a rural connection does not have.
- **Direct to `api.avodeck.com`** the same `PUT` is answered 401 after about 130 KB: auth rejects before
  the body is read. `GET /v1/cell` returns 200 on both hosts.

What applies now (Railway's documented limits, `docs.railway.com/networking/public-networking/specs-and-limits`):

| Limit | Railway |
|---|---|
| Request active | 15 minutes |
| Request idle (nothing sent either way) | 5 minutes |
| Request body must finish within | 5 minutes |
| Request body size | none documented |
| Headers, combined | 32 KB |

Consequences:

1. **The job stream is fed and ends early.** `api/app/core/routes/jobs.py` emits a `: keepalive` comment
   every `HEARTBEAT_SECONDS` (20) and ends at `JOB_STREAM_SECONDS` (240), both pinned by a test against
   `EDGE_SILENT_SECONDS` (60, community reports of a cutoff on a silent request) and `EDGE_IDLE_SECONDS`
   (300, Railway's idle close). `EventSource` reconnects on the clean end.
2. **An 8 MiB chunk now needs about 225 kbit/s** sustained upstream (8 x 1048576 x 8 bits / 300 s), so
   `CHUNK_SIZE` stays as it is.

**Still to measure on the live deployment** (the probes below are unauthenticated, so they create no data):
a 9 MiB `PUT` straight to `api.avodeck.com`; a request that takes longer than 120 s; and a job stream alive
past 120 s with heartbeats arriving. Write the numbers here.

## Uploads: limits to confirm before the first real bundle

A bundle is sent as 8 MiB chunks (`PUT /v1/uploads/{id}/chunks/{n}`), so every hop must carry a
**9 MiB** request body and a slow response. `POST /v1/uploads/{id}/complete` answers in milliseconds
(it enqueues a job and returns 202), and the browser then polls `GET /v1/uploads/{id}`, so no request
waits on finalisation. Confirm these on the real deployment and write the numbers in:

| Limit | Where | Needs | Measured |
|---|---|---|---|
| Request body size | Railway `api` | ≥ 9 MiB | |
| Response timeout | Railway `api` | ≥ 120 s | |
| Storage object size | Supabase Storage | ≥ 9 MiB per part | |
| Storage request rate | Supabase Storage | ~15 PUTs per 100 MB bundle, 3 at a time | |

How to measure, once `api.avodeck.com` is live (no case data needed; both calls are refused before
any body limit applies, so a 413 or 502 is the proxy, not the API):

```
head -c 9437184 /dev/zero > /tmp/9mib.bin
curl -i -X PUT --data-binary @/tmp/9mib.bin \
  -H 'content-type: application/octet-stream' \
  https://avodeck.com/v1/uploads/00000000-0000-4000-8000-000000000000/chunks/1
```

A `413` or an HTML error page from the edge means the body limit is below 9 MiB: raise it, or lower
`CHUNK_SIZE` on both sides (it is one constant in `api/app/core/routes/uploads.py` and the server
tells the browser the size, so only the server changes). For the timeout, call an endpoint that takes
two minutes to answer and check nothing between the browser and the API cuts it off.

Browser side, measured 2026-10-31 on the built page under the production CSP (`web/e2e/pipeline/upload-large.spec.ts`, a real 121 MB 900-page bundle): whole-file SHA-256 correct, 2.5 s to hash, 15 chunks totalling exactly the file size, 6 MB JS heap at the end, no CSP or WebAssembly violation. Nothing is buffered, so a 500 MB bundle should hash in about 10 s with the same memory.

`AVODECK_SCRATCH_DIR` and `AVODECK_SCRATCH_SLOTS` are read by `StoredDocument.spool()` (decrypted bytes
for seekable consumers; nothing calls it yet). `AVODECK_SCRATCH_DIR` is the scratch directory (default the
system temp; created `0700`), `AVODECK_SCRATCH_SLOTS` the number of spools at once (default 2, at least 1).
Files are unlinked the moment they are opened, so none survives a restart. Each spool logs a warning (a size
only) when the scratch disk has under 1 GiB free. Confirm Railway's disk encryption at rest before a consumer
adopts it (threat-model T-spool).

## Check a deploy

- `curl https://<api-service>.up.railway.app/v1/cell` returns the cell.
- The `cron` service's last run shows `exit 0` in its deployment list.
- The worker's logs show it waiting for jobs; upload a small test bundle and watch it move through Preparing.
