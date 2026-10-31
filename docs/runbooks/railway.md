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
- `APP_ORIGIN`: the web app's public origin (the Vercel domain).
- `PACKS_APPROVED_ONLY=1` once the launch packs are approved.

## Migrations

Not run by Railway. From the founder's Mac, before deploying code that needs them:
`supabase db push --linked`

## Web app (Vercel)

The browser must reach the API on its own origin (web/AGENTS.md). In `web/vercel.json`, add a rewrite once the Railway domain exists:

```json
"rewrites": [{ "source": "/v1/:path*", "destination": "https://api.avodeck.com/v1/:path*" }]
```

The api service's public name is `api.avodeck.com` (Railway custom domain, target port 8000). In Cloudflare it is a CNAME `api` to the value Railway shows, DNS only (grey cloud), plus the TXT `_railway-verify.api` Railway shows. The Railway name `api-production-0e15e.up.railway.app` still works.


One Vercel project (`avodeck-site`, root directory `web`) serves `avodeck.com`: `web/scripts/add-landing.sh` puts the landing page (`site/`) at `/` and `/privacy`, the app's `index.html` becomes `app.html` (the fallback for every other path), and `/v1/*` goes to Railway. `APP_ORIGIN` on Railway is `https://avodeck.com`.

Vercel rewrites do not carry WebSockets, so live dictation (`/v1/.../dictation`) needs the API on a subdomain of the web domain (for example `api.avodeck.com` behind the same site) before it works in the cloud. Everything else works through the rewrite.

## Uploads: limits to confirm before the first real bundle

A bundle is sent as 8 MiB chunks (`PUT /v1/uploads/{id}/chunks/{n}`), so every hop must carry a
**9 MiB** request body and a slow response. `POST /v1/uploads/{id}/complete` answers in milliseconds
(it enqueues a job and returns 202), and the browser then polls `GET /v1/uploads/{id}`, so no request
waits on finalisation. Confirm these on the real deployment and write the numbers in:

| Limit | Where | Needs | Measured |
|---|---|---|---|
| Request body size | Vercel rewrite `/v1/:path*` | ≥ 9 MiB | |
| Request body size | Railway `api` | ≥ 9 MiB | |
| Response timeout | Vercel rewrite | ≥ 120 s | |
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
