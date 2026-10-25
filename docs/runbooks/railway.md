# Railway services (UK cell, Amsterdam)

The API and worker run on Railway, Amsterdam (`europe-west4-drams3a`), and hold no data at rest (ADR 0021). All three services use the same image, built from the repository-root `Dockerfile`, and the same shared variables (`REGION`, `DATABASE_URL`, `DATABASE_URL_DIRECT`, Supabase and cell settings, `PACKS_DIR`). Each service points at its own config file (Settings, Config as code); the files are in `railway/`.

| Service | Config file | Start command | Schedule | Health check | Public networking |
|---|---|---|---|---|---|
| `api` | `railway/api.json` | image default (uvicorn) | none | `/v1/cell` | yes |
| `worker` | `railway/worker.json` | `python -m app.worker.main` | none | none | no |
| `cron` | `railway/cron.json` | `python -m app.worker.schedules` | `15 2 * * *` | none | no |

- `cron` runs every daily schedule (`registry.schedules`) once, catching up any missed dates, then exits 0 (or 1 if a schedule raised). Its logs hold schedule names and counts only.
- The worker's own housekeeping loop keeps running. `claim_scheduled_run` makes each date run once (F28), so running both is safe.
- Railway skips a cron run while the previous one is still running. The restart policy is `NEVER`, so a failed run waits for the next day's slot instead of retrying in a loop.
- Region: one replica in `europe-west4-drams3a` for `api` and `worker`.
