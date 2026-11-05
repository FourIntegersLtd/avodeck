# Load tests (k6)

Scripts for the load test in `docs/ops/scalability-plan.md` (PR 18). Targets come from ADR 0037 and NFR C3, C5, P2, P3 and P9. Results go in the run table in `docs/ops/capacity.md`.

## Safety rules

- **Load runs never against production.** Every script imports `lib/common.js`, which calls `requireStaging` (`lib/target.js`) when k6 starts. Without `STAGING_URL` the run stops before sending a request. The host must be `https`, end in `.up.railway.app` or `.avodeck.com`, contain the word `staging` as a whole token, and contain no `prod`, `production` or `live` token. `localhost` and `127.0.0.1` are allowed so you can rehearse against a laptop stack. A guard test (`api/tests/guards/test_foundation_load_scripts.py`) checks this list.
- **Synthetic data only.** Bundles come from `evals/` (`make eval-data`), stitched to 4,000 pages, half with text layers and half image-only. Use a separate staging Supabase London project and the Railway `staging` environment. Set `AI_PRIMARY_PROVIDER=bedrock` on staging before any live bundle.
- **No secrets in the repository.** Accounts, the metrics token and every address are passed with `-e` on the command line or read from a file outside the repository. Do not commit a users file.
- **No patient text in output.** The scripts print nothing from a response, tag requests with short route names (never case or page ids), and k6's summary holds only numbers. Do not turn on `--http-debug`.

## What you need

1. A staging environment running the build to test. `STAGING_URL` is its API origin only, for example `https://<your-staging-api>.up.railway.app` (no path).
2. A users file outside the repo, a JSON list of synthetic staging accounts, each with an `email`, a `password` and a `case_id`. Use at least 50 accounts for the bundle runs (empty cases) and 300 for the expert run (cases with a processed bundle), plus a few more for the interactive run.
3. For `bundles.js`: the stitched PDF (`LOAD_BUNDLE_FILE`), its SHA-256 (`LOAD_BUNDLE_SHA256`, from `shasum -a 256`) and the document kind its pack accepts (`LOAD_DOCUMENT_KIND`).
4. Optional: `OPS_METRICS_TOKEN` (the staging value of the bearer token for `/v1/internal/metrics`) so `experts.js` can check loop lag; `LOAD_WEB_ORIGIN` if staging checks the `Origin` header on writes.

## Runs

```
# 1. Bundles at once: 5, then 20, then 50. Pass: p90 <= 75 min, no failed job.
k6 run -e STAGING_URL=... -e LOAD_USERS_FILE=... -e LOAD_BUNDLE_FILE=... -e LOAD_BUNDLE_SHA256=... \
       -e LOAD_DOCUMENT_KIND=... -e LOAD_BUNDLES=5 scripts/load/bundles.js

# 2. Short jobs every 30 s while a bundle run is going (start this a few minutes after step 1).
#    LOAD_INTERACTIVE_OFFSET skips the accounts the bundle run is using. Pass: p95 time to start <= 30 s.
k6 run -e STAGING_URL=... -e LOAD_USERS_FILE=... -e LOAD_INTERACTIVE_OFFSET=60 scripts/load/interactive.js

# 3. 300 experts, ramped over 10 minutes. Pass: API p95 <= 400 ms, reader page p95 <= 300 ms,
#    5xx < 0.1%, 503 busy < 0.5%, loop lag p99 <= 100 ms.
k6 run -e STAGING_URL=... -e LOAD_USERS_FILE=... -e OPS_METRICS_TOKEN=... scripts/load/experts.js

# 4. Soak: the same run for 2 hours, with one worker deploy in the middle. Pass: no job fails, bundles resume.
k6 run -e STAGING_URL=... -e LOAD_USERS_FILE=... -e LOAD_HOLD=2h scripts/load/experts.js
```

Other settings: `LOAD_EXPERTS` (default 300), `LOAD_RAMP` (10m), `LOAD_HOLD` (20m), `LOAD_PAGES` (pages in each case, 4000), `LOAD_DURATION` (interactive, 90m), `LOAD_GIVE_UP_MINUTES` (bundle wait, 120), `LOAD_START_PROCESSING=1` (also call `POST /process` after the upload).

## What the scripts do not measure

Read these from Railway and the API's metrics endpoint while a run is going, and write them in the run table: worker memory against its limit (under 80%), retried 429s as a share of AI calls (under 5%), jobs that ran twice (none), and the dead-letter count. The expert run holds one event stream per user by renewing a 60-second request; the per-person cap of three streams is not exercised.

## Files

- `lib/target.js`: the staging allow-list (plain JavaScript, run by the guard test with Node).
- `lib/common.js`: sign-in, the request helper, shared counters.
- `bundles.js`, `interactive.js`, `experts.js`: the scenarios above.
