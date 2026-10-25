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

## Check a deploy

- `curl https://<api-service>.up.railway.app/v1/cell` returns the cell.
- The `cron` service's last run shows `exit 0` in its deployment list.
- The worker's logs show it waiting for jobs; upload a small test bundle and watch it move through Preparing.
