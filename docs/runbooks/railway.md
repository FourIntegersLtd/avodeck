# Railway: how the API and worker are deployed

ADR 0021. One Docker image (`api/Dockerfile`, built from the repository root), two services in one Railway project, region **europe-west4 (Amsterdam)**. There is no `railway.toml`; these are dashboard settings.

| Setting | `api` | `worker` |
|---|---|---|
| Source | this repo, branch `main` | same |
| Dockerfile path | `api/Dockerfile` | `api/Dockerfile` |
| Start command | (image default) | `python -m app.worker.main` |
| Health check path | `/v1/cell` | none |
| Public networking | yes (generate a domain) | no |
| Pre-deploy command | none | none |
| Volumes | none | none |
| Replicas | 1 | 1 |

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
"rewrites": [{ "source": "/v1/:path*", "destination": "https://api-production-0e15e.up.railway.app/v1/:path*" }]
```

Vercel rewrites do not carry WebSockets, so live dictation (`/v1/.../dictation`) needs the API on a subdomain of the web domain (for example `api.avodeck.com` behind the same site) before it works in the cloud. Everything else works through the rewrite.

## Check a deploy

- `curl https://<api-service>.up.railway.app/v1/cell` returns the cell.
- The worker's logs show it waiting for jobs; upload a small test bundle and watch it move through Preparing.
