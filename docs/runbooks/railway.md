# Railway: how the API and worker are deployed

ADR 0021. One Docker image (`api/Dockerfile`, built from the repository root), four services in one Railway project (`api`, `worker`, `worker-interactive`, `cron`), region **europe-west4 (Amsterdam)**. There is no `railway.toml`. The same settings are written as config-as-code in `railway/api.json`, `railway/worker.json`, `railway/worker-interactive.json` and `railway/cron.json`; in the dashboard, set each service's config file path (Settings > Config-as-code) to its file, or copy the values below by hand.

| Setting | `api` | `worker` (bulk) | `worker-interactive` | `cron` |
|---|---|---|---|---|
| Source | this repo, branch `main` | same | same | same |
| Dockerfile path | `api/Dockerfile` | `api/Dockerfile` | `api/Dockerfile` | `api/Dockerfile` |
| Start command | (image default; 2 uvicorn processes, see `WEB_CONCURRENCY` below) | `env WORKER_QUEUES=bulk python -m app.worker.main` | `env WORKER_QUEUES=interactive WORKER_CONCURRENCY=8 WORKER_DB_POOL_MAX=6 python -m app.worker.main` | `python -m app.worker.schedules` |
| Cron schedule | none | none | none | `15 2 * * *` (UTC, daily) |
| Health check path | `/v1/cell` | none | none | none |
| Public networking | yes (generate a domain) | no | no | no |
| Pre-deploy command | none | none | none | none |
| Volumes | none | none | none | none |
| Replicas | 2 | 1 | 1 | 1 (runs, then exits) |

## Job queues (scalability PR 7)

Jobs wait on one of two queues. `interactive` holds work a person is waiting for (`rerun_checks`, `export_case`); `bulk` holds everything else (bundle processing, report builds, deletions). The `worker-interactive` service takes only `interactive`, so a rerun or an export never queues behind a 4,000-page bundle. The `worker` service takes only `bulk`, and also drains the old `jobs` queue (work queued before the split, and pipeline "Try again" retries, which still use it). No database migration is needed.

`WORKER_QUEUES` is a comma list of `interactive` and/or `bulk`; anything else stops the worker at start with a message naming the setting. **Unset, a worker takes every queue**: that is the rollout state of the existing `worker` service, and it is also what a single-service setup (local, or a small cell) should do.

Rollout, in this order:

1. Deploy the new code to the existing `worker` service with `WORKER_QUEUES` still unset. Everything keeps working.
2. Create the `worker-interactive` service (same repo, config path `railway/worker-interactive.json`, same shared variables as `worker`). It starts taking `interactive` work at once.
3. Set the `worker` service's config path to `railway/worker.json` (start command `WORKER_QUEUES=bulk`) and redeploy it.
4. Check `GET /v1/internal/metrics`: `jobs_queued` shows `interactive`, `bulk` and (draining to 0) `jobs`.

### Connection budget

Every process holds database connections: the worker's own pool (`WORKER_DB_POOL_MAX`), the queue library's pool (`WORKER_QUEUE_POOL_MAX`) and, from the live-updates change, one listener connection in each API process. Add them up per pooler role and keep the total **below that role's pooler pool size** (Supabase: Database > Connection pooling), and keep the sum over both roles at least **10 below the database's direct `max_connections`** (Supabase Small: 90 direct, so about 80). Raise the pooler pool size first, then the numbers here.

| Service | Role | Replicas x processes | Pool | Queue pool | Listener | Connections |
|---|---|---|---|---|---|---|
| `worker` (bulk) | `app_worker` | 1 x 1 | 10 | 4 | 0 | 14 |
| `worker-interactive` | `app_worker` | 1 x 1 | 6 | 4 | 0 | 10 |
| **`app_worker` total** | | | | | | **24** (pooler pool size at least 30) |
| `api` | `app_api` | 2 x 2 | 8 | 0 | 1 | 36 |
| **`app_api` total** | | | | | | **36** (pooler pool size at least 40) |

The `api` service sets `WEB_CONCURRENCY=2` (uvicorn processes per replica, read by the image's start command; one if unset) and `API_DB_POOL_MAX=8` (each process's own pool). Each process holds its pool plus one listener, so 2 replicas x 2 processes x (8 + 1) = 36. `railway/api.json` sets 2 replicas; Railway variables cannot live in that file, so set the two variables by hand on the `api` service. `api/tests/guards/test_foundation_pool_budget.py` fails if this table, the two variables and the `railway/*.json` files disagree.

Final total 24 + 36 = 60, under a Small database's 80. The 15 default pooler pool size is too small for the `app_worker` role as soon as `worker-interactive` exists: **raise it to at least 30 before step 2 of the rollout**, and raise the `app_api` role's to at least 40 **before** the API goes to 2 replicas x 2 processes, or the pooler answers `EMAXCONNSESSION` and requests fail. A third worker, a bigger replica count or a bigger `WEB_CONCURRENCY` adds its own row here first.

Page-batch jobs (`PIPELINE_BATCH_PAGES` above 0, ADR 0038) share the bulk worker's pool by a hard bound: a batch-mode job (the parent and each child) holds at most `cap = max(1, (WORKER_DB_POOL_MAX - 2) // WORKER_CONCURRENCY)` pool connections at once, and every `bounded()` width in that mode is at most `cap`. With the pool at 10 and `WORKER_CONCURRENCY=4` the cap is 2: one bundle holds at most `window x cap + cap` = 2 x 2 + 2 = 6 connections, and any four batch-kind jobs together at most 4 x 2 = 8, the pool less its reserve of 2. The pipeline reads `WORKER_CONCURRENCY` itself (default 4, as the worker does), so change both together. A bigger `WORKER_CONCURRENCY` on the bulk worker needs a bigger `WORKER_DB_POOL_MAX` first, and that within the pooler's pool size and the table above. `PIPELINE_BATCH_WINDOW` stays at most `WORKER_CONCURRENCY - 1`; the worker warns at start when it is not. A lone bundle reads about 4 OCR pages at once instead of about 8 in one job today unless the pool grows; the account-wide OCR limiter is the real ceiling (`docs/ops/capacity.md`).

What is per process, and what is not (scalability PR 16):

- In each process: the pool, the `CaseNotifier` listener, the decrypted-words search cache and the case key cache. A case key destroyed in one process is forgotten in all of them by the Postgres notification (PR 15, migration 0066); the search cache is cleared the same way. Streams are pushed by the process that holds them, so a browser reconnecting to the other process still gets its updates.
- In Postgres, so shared by every process and replica: per-person rate limits and the cap of three open streams per person (PR 12, migration 0065), job state and progress.
- `GET /v1/internal/metrics` answers for the one process that took the request; the pool, loop-lag and 429 numbers are that process's. Read it several times, or add the numbers up, when judging the whole API.

The `cron` service runs each daily schedule once for today's date and exits (code 1 if any schedule failed; the others still run). A schedule already run for a date is skipped, so a retry or a second start is harmless. Its logs list schedule names and counts only.

## Variables

Copy every name from `api/.env.example` into both services (Railway "shared variables"), with cloud values. Never paste them into chat or commit them.

- Virus scanner: nothing to set. On Railway the API and workers detect the cell from `RAILWAY_PROJECT_ID` (Railway sets it) and always scan with ClamAV at `clamav.railway.internal:3310`. Do not set `VIRUS_SCANNER=recorded` in a cell: the process stops at start-up.
- `KEY_PROVIDER` and `LOCAL_MASTER_KEY` (the cell key): set on the `worker` and `worker-interactive` services as well as the `api`, **with the same value** (a Railway shared variable). Deferred work (emails and follow-up calls written in a request, audit 15 BG-16) is sealed under it; a worker without it stops at start-up with a message naming the missing setting.
- `DATABASE_URL` and `DATABASE_URL_DIRECT`: the Supabase **session pooler**, not the direct host (IPv6 only):
  `postgresql://<role>.<project-ref>:<password>@aws-0-eu-west-2.pooler.supabase.com:5432/postgres?sslmode=require`
  with `app_api` for `DATABASE_URL` and `app_worker` for `DATABASE_URL_DIRECT`.
- `DATABASE_URL_LISTEN` (API only, optional): `app_api` on the same Supabase **session pooler** URL as `DATABASE_URL` (port 5432) or a direct URL, **never transaction mode (port 6543)**, which cannot `LISTEN` (a 6543 URL stops the API at start-up with a clear error). With it set, each API process keeps one extra dedicated connection that Postgres notifies when a job on a case changes (migration 0062; the notification carries only the case id), so open screens update at once, a case key destroyed in another process is forgotten at once (migration 0066; without a listener a cached key is re-checked every 30 seconds) and the API stops looking at the database once a second per open screen. Count that one connection in the pool budget (API pool + 1 per API process) against the pooler's pool size. Unset, or if the connection drops, streams fall back to looking on a timer, so nothing breaks. `SSE_SAFETY_POLL_SECONDS` (default 30) is how often a pushed stream still looks on its own; every stream re-checks the person's access to the case at least every 60 seconds.
- `READER_SEARCH_CACHE_CASES` (default 8), `READER_SEARCH_CACHE_TTL_SECONDS` (default 600) and `READER_SEARCH_CACHE_MAX_WORDS` (default 3000000), API only: the bundle search keeps each case's opened words in API memory (about 100 MB per million words, so the default holds about 300 MB at most), per process. Lower the word cap if API memory gets tight; the words are patient text and never leave the process or reach a log.
- `WORKER_DB_POOL_MAX` (default 10) and `WORKER_QUEUE_POOL_MAX` (default 4): the worker's two connection pools. The session
  pooler lets only its **pool size** of clients in at once per role (Supabase default 15, set under Database > Connection
  pooling). Keep `WORKER_DB_POOL_MAX + WORKER_QUEUE_POOL_MAX` below the pooler's pool size, or the pooler answers
  `EMAXCONNSESSION` and jobs stop. Raise the pooler's pool size first, then these. Model calls in flight follow
  `WORKER_DB_POOL_MAX` (less a reserve of two): with the defaults, only about **8 model calls run at once**, on 10 + 4
  pooler connections per worker replica. A **second worker replica**, or any other client connecting as the same pooler
  role (`app_worker`), adds its own connections to the same pool of 15 and needs more pooler capacity first: raise the
  Supabase pool size, then `WORKER_DB_POOL_MAX` / `WORKER_QUEUE_POOL_MAX`, keeping every replica's two pools together
  below the pooler's pool size. More model calls at once need a bigger `WORKER_DB_POOL_MAX`, never a bigger reserve.
- `WEB_CONCURRENCY` (default 1) and `API_DB_POOL_MAX` (default 10), API only: uvicorn processes per replica, and the database pool of each process. The `api` service sets 2 and 8 (connection budget above); change them only together with that table.
- `API_DB_ACQUIRE_TIMEOUT_SECONDS` (default 2), `API_DB_MAX_WAITING` (default 50), `API_DB_STATEMENT_TIMEOUT_MS` (default 5000, 0 = off): the API pool fails fast. A request that cannot get a connection in time, finds more than that many already waiting, or runs a statement past the limit gets 503 `busy` with `Retry-After: 2` (failure mode F53). The worker's pool has none of these limits.
- `OPS_METRICS_TOKEN`: long random string. `GET /v1/internal/metrics` with `Authorization: Bearer <token>` returns the API's pool, event-loop lag and provider 429 counts, plus queue depth, oldest wait and job run times from the database (numbers only). Unset, the route answers 404. The worker logs the same snapshot every 60 seconds. Generate with `openssl rand -base64 32`; never paste it into chat.
- `SENTRY_TRACES_SAMPLE_RATE` (default 0.05, 0 to 1): the share of requests Sentry traces. Events are still scrubbed.
- `AI_ENTRY_COVERAGE_FLOOR` (default 0.8, more than 0 and at most 1): the share of a page's words the entries read from it must cover.
- `AI_COST_GUARD_MULTIPLE` (default 3), `AI_EXPECTED_TOKENS_PER_PAGE` (4000), `AI_MIN_EXPECTED_PAGES` (100): the cost guard
  (F11). The worker pauses a case once its tokens pass multiple x tokens per page x max(pages, minimum pages), and the
  API reads the same three to tell the person "this case has used up its allowance" and to refuse "Try again". Set them
  **identically on the api and the worker services** (use Railway shared variables, never per-service copies): if they
  differ, a screen can offer "Try again" for a run the worker will pause again, or hide it for one that would work.
  Both read them through `cost_guard()`, so the defaults and the sum live in one place (`app/core/config.py`).
- `APP_ORIGIN`: the web app's public origins, comma-separated (`https://avodeck.com`). Never a wildcard; start-up refuses one.
- `COOKIE_DOMAIN`: `avodeck.com`, so the page on `avodeck.com` can read the `av_csrf` cookie the API sets (ADR 0031). Empty locally.
- `PACKS_APPROVED_ONLY=1` once the launch packs are approved.

## Alerts (Sentry)

Create these alert rules in Sentry (EU) for the production project. The numbers come from `GET /v1/internal/metrics` (see `OPS_METRICS_TOKEN` above); point an uptime check or a scheduled job at it and send the values to Sentry as metrics, or read them by hand during a bundle run.

| Alert | Condition | What to do |
|---|---|---|
| Interactive queue waiting | `oldest_queued_seconds` for the interactive queue over 120 s | A person is waiting: check the worker is up, `jobs_queued`, and `provider_429_total` |
| Bulk queue waiting | `oldest_queued_seconds` for the bulk queue over 900 s | Add worker capacity or check the provider limits |
| Dead-lettered job | any Sentry event with message `job_dead_lettered` (tags `kind`, `job_id`: no case, name or text) | Open the job in the support console; requeue it or tell the expert |
| Pool exhausted | `pool_waiting` above 0 for 60 s | Raise the Supabase pooler size, then `API_DB_*` limits; check for a slow statement |

The queue names in the metrics are `interactive`, `bulk` and, until it drains after the rollout, the old `jobs`. Use the interactive thresholds for `jobs` too.

## Migrations

Not run by Railway. From the founder's Mac, before deploying code that needs them:
`supabase db push --linked`

## Web app (Vercel)

The browser calls the API **directly** on `https://api.avodeck.com` (ADR 0031). Vercel serves static files
only and proxies nothing, so Vercel's 120 s limit on a proxied request no longer applies to anything.

Required on the Vercel project, **Production** only (a Config value, not a Secret: it is baked into the public bundle):

```
VITE_API_ORIGIN=https://api.avodeck.com
```

It is baked into the bundle at build time by `cspPlugin` (`web/csp.ts`), so a change needs a redeploy, and
an empty value ships a build that calls a dead `/v1` path on the web host.

**Preview deployments cannot sign in.** A `*.vercel.app` preview is cross-*site* to `api.avodeck.com`, so
the `SameSite=Strict` session cookie is neither stored nor sent, and adding a preview origin to
`APP_ORIGIN` stops the API booting (`COOKIE_DOMAIN` must cover every allowed origin). Test against
production or locally.

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

`WEB_CONCURRENCY=2`, `API_DB_POOL_MAX=8` and the 30 s draining time for the `api` service are set in `railway/api.json` (start command and `drainingSeconds`), so there is nothing to set by hand for them. `DATABASE_URL_LISTEN`, `DATABASE_URL_DIRECT` and `OPS_METRICS_TOKEN` hold credentials and are set in the Railway dashboard only.
