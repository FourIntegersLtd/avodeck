# Avodeck scalability: implementation plan (step by step, one PR at a time)

## Context
The user asked for a senior-engineer audit of what stops the backend from scaling to many concurrent users, and a plan detailed enough that a cheaper model can carry it out alone. Two earlier plans exist (`~/.claude/plans/crystalline-kindling-panda.md` and `pasted-content-id-d93f-written-for-lazy-dolphin.md`, both 4 Nov). I re-checked the code on 5 Nov: **none of their fixes has landed**. The pool still has no timeout (`api/app/core/db.py:71-81`), Bedrock is still 5 rpm (`config.py:415`), SSE still polls the database (`core/sse.py:60`, `routes/case_events.py:35-50`, `main.py:148`), the API still runs `--workers 1` (`api/Dockerfile:72`), and every Railway service still has `numReplicas: 1`. The findings stand, with one addition: the worker fetches jobs by polling (`jobs.py:317` `listen_notify=False`). That is server-side and acceptable; leave it.

This file replaces both earlier plans. It turns the audit into **18 small PRs**, in order. Each PR lists the owner, branch, files, tests to write first, exact steps and a "done when" check. Once this is approved, the first action is to copy it to `docs/ops/scalability-plan.md` (a docs-only PR) so every owner agent can read it.

### Target (to go into NFR C3/C5 by PR 2)
- 1,000 experts registered, 300 online at peak, 50 bundles processing at once.
- 4,000-page bundle in 75 min or less at p90 (P2). API p95 400 ms or less (P9). Reader page p95 300 ms or less (P3).
- A short job a person is waiting for starts within 30 s.

### What breaks first today (from the audit, estimates)
| Load | First break | What the expert sees |
|---|---|---|
| 1 bundle | Bedrock 5 rpm, about 480 calls per 4,000 pages | about 96 min, which fails P2 |
| 2+ bundles | The slot count is per case through RLS, so AWS returns 429 | "Paused, service busy", then failed after about 2 h |
| 4 bundles | 4 worker slots, one queue | Rerun checks and exports wait hours |
| 100 online | Reader search decrypts about 1.6M words on the event loop | Every page hangs while anyone searches |
| 300 online | Pool of 10, about 150 SSE transactions/s, no acquire timeout | p95 over 400 ms, then 30 s hangs |

---

## Rules for the implementer (read before every PR)
1. **One PR = one numbered section below.** Branch name as given. Do not combine PRs.
2. **Test first** (AGENTS.md): write the listed tests, run them, see them fail for the right reason, commit `test(<slice>): …`; then the code, commit `feat(<slice>): …`. Never edit a test to make it pass.
3. **Owners.** Anything in `api/app/core/`, `railway/`, `api/Dockerfile`, `supabase/migrations/00xx`, `.github/` belongs to **foundation-owner**. `features/pipeline/` belongs to pipeline-owner, `features/reader/` to reader-owner, `features/understand/` to understand-owner, `features/close/` to close-owner. Hand each PR to its owner agent and do not pass `model`.
4. **Line numbers drift.** Find code by the **symbol** named here (`grep -n "def put_chunk" -r api/app`), not by line number.
5. **Never break:** rule 3 (blind read, 423), rule 4 (browser talks only to FastAPI), rule 6 (no patient text in logs, metrics or NOTIFY payloads: ids and counts only), rule 9 (access check on every case route; never widen RLS; use SECURITY DEFINER functions with `set search_path = ''`), the no-polling rule (the browser never polls; server-side waits are fine).
6. **Migrations:** use the `add-migration` skill. Foundation range 0000–0099; find the next free number with `ls supabase/migrations | grep -E '^00[0-9]{2}_' | tail -1`. Every migration gets a down file in `supabase/migrations/down/`.
7. **Test DB:** never ports 55322/56322. Set `TEST_DATABASE_*` first (memory: pytest hits the app DB).
8. **Before push:** touched tests + `api/tests/guards/` + `make lint` + `python scripts/check_spec.py`. CI runs the full suite. Then a `reviewer` agent pass on the diff, then watch CI to green and merge.
9. Every new setting is read from the cell config / env through `app/core/config.py`, with a default equal to today's behaviour unless the PR says otherwise.

---

## Phase 0: accounts and settings (the user, no code)
Do these first. Several are lead times outside our control.
- **AWS Bedrock (eu-west-2, in-region model IDs only):** in Service Quotas, request about 350 requests/min, about 12M input TPM and about 2.5M output TPM for the Sonnet and Opus IDs in use. Ask about provisioned throughput.
- **Azure Document Intelligence (UK South):** request S0 TPS above 15, or create a second resource. The plan supports N endpoints (PR 4).
- **Supabase:** Pro plan, Small compute or larger, IPv4 add-on. Raise the pooler pool size (Database → Settings → Connection pooling) to at least 40.
- **Railway:** confirm the memory limit per service and the deploy kill grace. Create a `staging` environment (used in PR 18).
- **Before any live bundle:** `AI_PRIMARY_PROVIDER=bedrock` (memory note).
- Keep `WORKER_CONCURRENCY=4` until PR 5 and PR 6 merge.

---

## PR 1. Pool safety: fail fast, never hang (foundation, S)
Branch `fix/pool-acquire-timeout`. Files: `api/app/core/db.py`, `api/app/core/config.py`, `api/app/core/errors.py` (or wherever `ApiError` codes live), web i18n for the new code.
Tests first (`api/tests/unit/test_foundation_pool_limits.py`, `api/tests/http/test_foundation_pool_busy.py`):
- With `max_size=1`, holding the one connection, a second `database.request(...)` raises `PoolBusy` within `acquire_timeout + 0.5` s.
- An HTTP route hitting a busy pool returns **503**, body code `busy`, header `Retry-After: 2`.
- A statement longer than `statement_timeout` on an API request is cancelled; the route returns 503 `busy` (not 500).
- The worker `Database` has no statement timeout (bundle steps are long).
Steps:
1. Add settings `API_DB_ACQUIRE_TIMEOUT_SECONDS` (default 2.0), `API_DB_MAX_WAITING` (default 50), `API_DB_STATEMENT_TIMEOUT_MS` (default 5000; 0 means off).
2. In `Database.__init__` add keyword args `acquire_timeout: float = 30.0`, `max_waiting: int = 0`, `statement_timeout_ms: int = 0`; pass `timeout=acquire_timeout, max_waiting=max_waiting` to `AsyncConnectionPool`.
3. In `Database.request` (and `Database.system`-style helpers, if present), catch `psycopg_pool.PoolTimeout` and `psycopg_pool.TooManyRequests` and raise `ApiError(503, "busy", headers={"Retry-After": "2"})`. If `statement_timeout_ms > 0`, add `set local statement_timeout = <n>` to the same round trip that already sends `BEGIN` and the `app.person_id` setting. Catch `psycopg.errors.QueryCanceled` and raise the same 503.
4. In `app/main.py`, build the API `Database` with the three settings. Leave `worker/main.py` unchanged.
5. Add an i18n key for `busy`: "Avodeck is busy. Please try again in a moment." Add the code to `docs/spec/failure-modes.md`.
Done when: tests green; a manual `ab -c 50` against `/v1/cell` with pool 2 gives 503s, not hangs.

## PR 2. Measure: metrics, ops endpoint, alerts, targets (foundation, M)
Branch `feat/ops-metrics`. Files: new `api/app/core/metrics.py`, `api/app/core/db.py`, `api/app/core/jobs.py`, `api/app/core/ai/kit.py`, `api/app/core/logs.py`, new `api/app/core/routes/ops.py`, `docs/spec/non-functional-requirements.md`, new `docs/decisions/0037-capacity-plan.md`.
Tests first:
- `metrics.snapshot()` returns `pool_size`, `pool_available`, `pool_waiting`, `loop_lag_ms_p99`, `provider_429_total{bedrock,azure_ocr}`, `jobs_queued{queue}`, `oldest_queued_seconds{queue}`, `job_seconds_p95{kind}`. Values only, no ids or text (a guard test greps the snapshot for strings longer than 40 chars).
- `GET /v1/internal/metrics` returns 404 without the `OPS_METRICS_TOKEN` bearer header and 200 with it.
- The dead-letter check: a job ending `failed` after `max_attempts` sends one Sentry event `job_dead_lettered` with kind and job id only.
Steps:
1. `metrics.py`: an in-process registry (counters plus a small ring buffer for percentiles). No new dependency.
2. Pool stats come from `AsyncConnectionPool.get_stats()`. Loop lag: a background task that sleeps 0.5 s and records how late it wakes.
3. Queue stats: one query `select queue_name, count(*), extract(epoch from now()-min(scheduled_at)) from procrastinate_jobs where status='todo' group by 1`, run at most every 10 s and cached.
4. Count 429s where the Bedrock/Anthropic and Azure clients already map 429 to `service_busy`.
5. Sentry `traces_sample_rate` reads `SENTRY_TRACES_SAMPLE_RATE` (default 0.05) in `core/logs.py`; the existing scrubber still runs.
6. Alerts (Sentry alert rules, documented in `docs/runbooks/railway.md`): interactive queue oldest > 120 s, bulk queue oldest > 900 s, any `job_dead_lettered`, `pool_waiting > 0` for 60 s.
7. ADR 0037 records the targets above. Update NFR C3 to 50 and C5 to 1,000.
Done when: tests green and the endpoint shows live numbers on dev.

## PR 3. Account-wide Bedrock slot count (foundation, S)
Branch `fix/ai-slots-account-wide`. Files: new migration `00xx_foundation_ai_slots_count.sql`, `api/app/core/ai/kit.py` (`_slot_attempt`), `api/app/core/config.py`.
Problem: the slot `select count(*) from app.ai_request_slots …` runs as `app_worker` under RLS, so it only sees the current case's rows. Five bundles each think they are alone, and AWS returns 429.
Tests first (`api/tests/repository/test_foundation_ai_slots_global.py`):
- Two jobs on **two different cases**, rpm limit 1: the second job is not granted a slot within the window.
- Concurrency limit 1 across two cases: the second waits until the first releases.
- `app_worker` still cannot `select` another case's `ai_request_slots` rows directly (RLS unchanged).
Steps:
1. Migration: `create function app.ai_slots_in_use(p_provider text, p_window interval) returns table(in_flight int, granted_in_window int) language sql security definer set search_path = '' stable as $$ … $$;` counting all rows for the provider (granted and not released, and granted within the window). `revoke all … from public; grant execute … to app_worker;`.
2. In `_slot_attempt`, replace both count queries with one call to the function. Keep the advisory lock.
3. Make `BEDROCK_CONCURRENCY` an env setting (`BEDROCK_CONCURRENCY`, default 3). Fix the docstring that says "account-wide".
4. After the quota is granted (Phase 0), the user sets `BEDROCK_RPM_LIMIT` and `BEDROCK_CONCURRENCY` in Railway. Starting point: concurrency = rpm × average call seconds ÷ 60.
Done when: tests green.

## PR 4. One shared Azure OCR limiter (foundation + pipeline, M)
Branch `feat/ocr-global-limit`. Files: `api/app/core/ai/kit.py` (or the OCR client in `app/core/`), `api/app/core/config.py`, `api/app/features/pipeline/service.py` (`OCR_CONCURRENCY`).
Tests first:
- Provider `azure_ocr` with `per_second=2`: 3 submits from 2 jobs within one second, so the third waits for the next second.
- OCR for a 30-page fixture still finishes and keeps page order.
Steps:
1. Reuse the slot scheduler with `provider="azure_ocr"`. Add a window setting so the limit can be per second (`AZURE_OCR_TPS`, default 12, under S0's 15) and a concurrency cap (`AZURE_OCR_CONCURRENCY`, default 30).
2. Use the **detached** variant (`_wait_for_slot_detached`), so no connection is held while waiting.
3. Keep the per-job semaphore in `service.py`, but set it from the setting.
4. Optional (only if Azure grants no TPS raise): accept a list of endpoints in the cell config and spread jobs across them by `hash(job_id)`.
Done when: tests green and dev metrics show the `azure_ocr` 429 count stays at 0 on a 900-page run.

## PR 5. Keep the worker loop free; stalls are not failures (pipeline + foundation, S)
Done, with a leftover closed: stall requeues are counted (`jobs.stall_requeues`, migration 9002) and capped by `JOB_MAX_STALL_REQUEUES` (default 5); the next stall fails the job (`stalled_too_often`) and dead-letters it.
Branch `fix/worker-loop-blocking`. Files: `features/pipeline/service.py` (`rasterise`, `_unpack`, pikepdf use), `api/app/core/jobs.py` (stall requeue, attempts), `api/app/worker/main.py`.
Tests first:
- A test patches `rasterise` to `time.sleep(2)`; the job's heartbeat timestamp still advances during it.
- A job requeued after a stall (heartbeat stopped) keeps its `attempts` value; a job that raised still adds 1.
- On SIGTERM the worker stops taking jobs and, within `WORKER_SHUTDOWN_GRACE_SECONDS`, marks running jobs `paused` (with no attempt counted), so they resume on restart.
Steps:
1. Module-level `_PDFIUM = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")` (PDFium is not thread-safe). Every PDFium call goes through `await loop.run_in_executor(_PDFIUM, fn, …)`.
2. pikepdf and `_unpack` go through `asyncio.to_thread`.
3. In `jobs.py` find `attempts = attempts + case when status = 'paused' then 0 else 1 end`. Add a `requeued_stall` flag set by `requeue_stalled()` and orphan requeue, and treat it like `paused` (no increment). Clear the flag on start.
4. Add `WORKER_SHUTDOWN_GRACE_SECONDS` (default 25) and pass it to procrastinate's `shutdown_graceful_timeout`. Also set it in `railway/worker.json` (`"drainingSeconds"` if Railway supports it; otherwise document it).
Done when: tests green.

## PR 6. Bound worker memory (pipeline, M)
Branch `fix/pipeline-split-memory`. Files: `features/pipeline/service.py` (the `b"".join` of document parts and the per-page PDF `cache`).
Tests first:
- A split of a generated 1,500-page PDF keeps `tracemalloc` peak under 300 MB (set the number from a run before the change and take 40% of it).
- The split output (page count, page hashes) is unchanged against the current fixture.
Steps:
1. Write the document's parts to a `tempfile.NamedTemporaryFile(dir=<scratch>)` as they are read from S3; open pikepdf/PDFium from the path, never from one big `bytes`.
2. Remove the all-pages `cache`. Produce each page PDF, upload it, and drop it. If a later step needs it, read it back from S3.
3. Cap concurrent splits per process at 1 (`asyncio.Semaphore(1)` at module level).
4. Delete the temp file in `finally`.
5. After merge, measure RSS on a 900-page dev bundle. If RSS per job stays under 400 MB, raise `WORKER_CONCURRENCY` to 8, then to 12 only after PR 7.
Done when: tests green and the RSS is recorded in `docs/ops/capacity.md`.

## PR 7. Separate interactive and bulk work (foundation + understand + close, M)
Branch `feat/worker-queues`. Files: `api/app/core/jobs.py` (`register_job`, queue name), `api/app/worker/main.py`, new `railway/worker-interactive.json`, `features/understand/jobs.py` (`rerun_checks`), `features/close/jobs.py` (`export_case`), `docs/runbooks/railway.md`.
Tests first:
- A job registered with `interactive=True` is deferred to queue `interactive`; others go to `bulk`.
- A worker started with `WORKER_QUEUES=interactive` never takes a `bulk` job.
- `rerun_checks` and `export_case` are registered interactive.
- With 4 bulk jobs running, an interactive job starts within 1 s on the interactive worker.
Steps:
1. `register_job(..., interactive: bool)` already exists as a flag. Map it to procrastinate `queue="interactive"` or `"bulk"`. Keep the old queue name readable during rollout: a worker with no `WORKER_QUEUES` takes all queues.
2. `worker/main.py` reads `WORKER_QUEUES` (comma list) and passes `queues=[…]` to `run_worker_async`.
3. `railway/worker-interactive.json`: same image, `startCommand` `python -m app.worker.main`, env `WORKER_QUEUES=interactive`, `WORKER_CONCURRENCY=8`, `WORKER_DB_POOL_MAX=6`. The bulk worker gets `WORKER_QUEUES=bulk`.
4. Pool budget (write it into the runbook): total = Σ per process (`WORKER_DB_POOL_MAX` + `WORKER_QUEUE_POOL_MAX`) + API (pool + 1 listener, PR 8). It must stay under the Supabase pooler pool size and direct max connections minus 10.
Done when: tests green and both services are deployed on dev.

## PR 8. Push, not poll, for live updates (foundation, M)
Branch `feat/sse-listen-notify`. Files: new migration `00xx_foundation_jobs_notify.sql`, new `api/app/core/notify.py`, `api/app/core/sse.py`, `api/app/core/routes/case_events.py`, `api/app/core/routes/jobs.py`, `api/app/main.py`, `api/app/core/config.py`. **No web change**: `web/src/foundation/lib/sse.ts` is untouched.
Tests first:
- A job update fires `pg_notify('case_jobs', '<case_id>')`. The payload is exactly a uuid (rule 6 guard).
- An open case stream receives the new state within 1 s of the update, with **no** fetch in between (count the `fetch` calls: 1 at connect, 1 per notify, plus 1 per safety-net tick).
- With no notify, the stream still fetches every `SSE_SAFETY_POLL_SECONDS` (default 30) and sends keepalives every 20 s.
- Access is re-checked at least every 60 s: removing the member ends the stream within 61 s.
- The listener reconnects after its connection drops, and wakes every stream once after reconnecting (so nothing is missed).
- The existing no-polling guard tests still pass.
Steps:
1. Migration: trigger `after insert or update of status, step, pct, step_done, step_total, error_code on app.jobs for each row` → `perform pg_notify('case_jobs', new.case_id::text)` when `new.case_id is not null`.
2. `notify.py`: `class CaseNotifier` with one dedicated `psycopg.AsyncConnection` (autocommit, **not** from the pool) on a new setting `DATABASE_URL_LISTEN`. This must be a direct or **session-mode** pooler URL (port 5432), as `app_api`, because transaction mode cannot LISTEN. Keep `dict[case_id, set[asyncio.Event]]`; `subscribe(case_id) -> Event` and `unsubscribe`. A background task `async for n in conn.notifies(): set every event for n.payload`, with reconnect and backoff, setting all events after a reconnect.
3. Start and stop it in the FastAPI lifespan in `main.py`. If `DATABASE_URL_LISTEN` is unset (tests, local), fall back to today's timed loop.
4. `sse_stream(..., wake: asyncio.Event | None = None)`: when given, replace `asyncio.sleep(poll)` with `wait_for(wake.wait(), timeout=min(safety, heartbeat_left, deadline_left))`, then `wake.clear()`. Coalesce: never fetch more than once per 0.5 s.
5. The case stream and the job stream subscribe by case id. Split `_jobs` so the access check runs at connect and then at most every 60 s, not every tick.
6. Pool budget: add 1 direct connection per API process.
Done when: tests green, and dev metrics show SSE transactions per second near 0 when idle.

## PR 9. No connection held across slow I/O; CPU off the loop (foundation + reader + pipeline, S)
Branch `fix/no-conn-across-io`. Files: `api/app/core/routes/uploads.py` (`put_chunk`), `features/reader/router.py` (`page_image`), `api/app/core/crypto.py` call sites for large blobs, `features/pipeline/router.py` (`pii.strip` loop).
Tests first:
- `put_chunk` with a patched slow S3 put (1 s): the pool's in-use count is 0 during the put (spy on `Database.request`).
- Two concurrent `put_chunk` calls for the same upload and part still end with exactly one stored part (the existing race tests in `test_pipeline_upload_races.py` must still pass).
- `page_image` with a slow S3 get holds no connection during the get, and still returns 404 to a non-member and 423-free image to the named expert as today.
Steps:
1. `put_chunk`: (a) short transaction: access check plus load upload state, then release; (b) read the body, SHA-256 and AES-GCM in `asyncio.to_thread`, then put to S3 under a key that includes the part number and hash; (c) short transaction: advisory lock, recheck state, record the part (idempotent on part number + hash), commit. On a conflict, delete the orphan S3 object or leave it to the existing sweep (`test_foundation_upload_sweep.py`).
2. `page_image`: transaction for access plus key lookup; release; then the S3 get and decrypt in `to_thread`.
3. Wrap AES-GCM on anything over 64 KiB and the `pii.strip` loop in `asyncio.to_thread`.
Done when: tests green.

## PR 10. Fast bundle search (reader + foundation contract, M)
Branch `fix/reader-search-cache`. Files: `features/reader/router.py` (search route), `api/app/core/contracts/document_words.py`, new `features/reader/search_cache.py`.
Tests first:
- Two searches on the same case decrypt the words once (count `crypto.decrypt` calls).
- The cache is bounded (`READER_SEARCH_CACHE_CASES`, default 8; LRU), expires after 10 min, and is dropped when pages are added to the case or the case is destroyed.
- Results are identical to today's on the fixture bundle.
- A search on a 4,000-page fixture keeps loop lag p99 under 100 ms (decrypt runs in `to_thread`).
- Rule 3: search still respects the blind-read rules exactly as today. Change only how words are loaded, not what is returned.
Steps:
1. `search_cache.py`: `get_words(case_id, loader)` returns a cached list of `(page_number, word_id, text_lower, polygon)`. The loader decrypts in a batch in `to_thread`, not one `await` per word.
2. The case key cache invalidation (PR 15) also clears this cache.
3. Memory note: about 1.6M words × about 60 bytes is about 100 MB per case at worst. The default of 8 cases bounds it. Lower it if API RSS gets tight.
Done when: tests green.

## PR 11. Timeline filtered and paged in SQL (understand, M)
Branch `fix/timeline-sql-paging`. Files: `features/understand/repo.py`, `features/understand/routes_timeline.py`, then `make api-client`.
Tests first:
- `GET …/timeline?limit=200` returns at most 200 items plus `next_cursor`; following cursors returns every item once, in the same order as today.
- Filters (lane, date range, kind) give the same results as today's Python filter on the fixture case.
- With no `limit`, the response is the same shape and contents as today (contracts only grow).
Steps:
1. Move each Python filter into the SQL `where`. Order by `(date, id)` and use keyset `where (date, id) > (%s, %s)`.
2. Add optional `limit` (max 500) and `cursor` query params, and a `next_cursor` field.
3. The web timeline (understand capability) requests pages as the user scrolls. That is fetch on user action, not polling.
Done when: tests green and the client is regenerated.

## PR 12. Per-person rate limits and stream cap (foundation, S)
Branch `feat/rate-limits`. Files: `api/app/core/` (find the existing upload limiter: `grep -rn "rate" api/app/core | grep -i limit`), `api/app/core/sse.py`.
Tests first:
- A person opening a 4th SSE stream gets 429 `too_many_streams`; closing one frees a place.
- A person over `API_RATE_PER_MINUTE` (default 600) gets 429 `slow_down` with `Retry-After`; another person is unaffected.
- Sign-in and upload limits behave as before.
Steps:
1. Reuse the existing limiter's storage. If it is in memory, add a note that each process counts separately, which is acceptable at 2 processes.
2. Stream cap: in-process counter per person, decremented in the generator's `finally`.
3. i18n keys for both codes.
Done when: tests green. Update `docs/decisions/deferred.md` to remove the rate-limit item.

## PR 13. Export without buffering the whole archive (close, S)
Done, with a leftover closed: the final put streams the sealed temp file as an S3 multipart upload (`CaseFileStorage.put_file`, 8 MiB parts, aborted on failure), so no step holds the archive whole.
Branch `fix/export-streaming`. Files: `features/close/jobs.py` (`export_case`, the `.read()` of the zip).
Tests first: an export of the fixture case has the same bytes after decryption as today, and peak memory under 2× the largest single file (tracemalloc).
Steps: write the zip to a temp file; seal and put in chunks using the same envelope scheme as uploads (`core/uploads.py`); delete the temp file in `finally`.

## PR 14. Missing indexes (foundation, S)
Branch `chore/hot-indexes`. One migration: `create index concurrently if not exists jobs_case_created_idx on app.jobs (case_id, created_at desc, id);` and `pages_bundle_document_idx on app.pages (bundle_id, document_id);`. Use `concurrently` only if the migration runner allows it outside a transaction; otherwise plain `create index`. Test: `explain` of the case-stream query uses the new index (repository test).

## PR 15. Key cache cleared on destroy (foundation, S)
Branch `fix/key-cache-destroy`. Files: `api/app/core/crypto.py` (the 5-min key cache), `api/app/core/notify.py`.
Tests first: after a case key is destroyed, a decrypt on the API fails within 1 s, not after 5 min.
Steps: the destroy path runs `pg_notify('case_key_destroyed', case_id)`; `CaseNotifier` listens on that channel too and evicts the key and the search cache. A safety net: on a cache hit older than 30 s, check `destroyed_at` once.

## PR 16. API on 2 processes and 2 replicas (foundation, S; after PRs 1, 8, 12)
Branch `chore/api-processes`. Files: `api/Dockerfile`, `railway/api.json`, `docs/runbooks/railway.md`.
Steps:
1. Dockerfile CMD: `--workers ${WEB_CONCURRENCY:-1}`.
2. Pool per process: `API_DB_POOL_MAX` (new setting, default 10). Set `WEB_CONCURRENCY=2`, `API_DB_POOL_MAX=8`. With 2 replicas that is 32 pooled connections plus 4 listeners. Check the budget from PR 7.
3. `numReplicas: 2`. Confirm nothing keeps per-process state that must be shared: login rate limit (PR 12 note), SSE (per-process listener, fine), key cache (PR 15, fine).
Test: a test that `main.py` reads `API_DB_POOL_MAX`. Then deploy to staging and run the PR 18 load test.

## PR 17. Bundle split into page-batch jobs; narrow the case lock (pipeline + foundation, L; last)
Branch `feat/pipeline-batch-jobs`. **Design first**: pipeline-owner writes a one-page design in `docs/slices/A-pipeline.md` (Opus session, per CLAUDE.md), the reviewer checks it, then build.
Shape to design toward:
- `process_bundle` keeps the steps that change shared case state (split, dedupe, finish, timeline publish) under `case:<id>`.
- OCR and extraction run as child jobs `process_pages(bundle_id, first, last)` of about 100 pages each, registered with `changes_case=False`, on the `bulk` queue. They reuse the per-page checkpoints (`pages_without_words`, `pages_to_extract`), so a retry redoes only the missing pages.
- The parent waits for its children without holding a slot: it ends after queuing them, and the last child to finish (counted in one SQL `update … returning remaining`) queues the `finish_bundle` job.
- Progress: every child calls `ctx.progress` on the **parent** job row, so SSE and the browser are unchanged.
Tests first: an expert's `rerun_checks` and `build_report` run while their bundle is being read; killing one child mid-way redoes only its missing pages; a 900-page fixture produces the same facts as the single-job path.

## PR 18. Load test, staging, docs clean-up (foundation, M)
Branch `chore/load-test`. Files: new `scripts/load/` (k6 scripts plus a README), `docs/ops/capacity.md`, and the stale `.do/app.yaml`, root `Dockerfile` and DigitalOcean wording in AGENTS.md (the stack row and rule 6) and ADR 0021/0036 numbers. Changing AGENTS.md needs the user's approval.
Load test on the Railway `staging` environment with a separate staging Supabase London project and synthetic bundles only (`evals/`, stitched to 4,000 pages, half with text layers and half image-only):
- **Bundles:** 5, then 20, then 50 at once. Pass: p90 at most 75 min, 0 failed jobs, retried 429s under 5% of calls, worker RSS under 80% of the limit, no job run twice.
- **Interactive:** `rerun_checks`, `build_report`, `export_case` and `build_trial_pack` every 30 s during the bundle run. Pass: p95 time to start at most 30 s.
- **Experts:** 300 virtual users ramped over 10 min. Each holds 1 case stream, reads a page every 20 s (page, image, facts), searches every 5 min and opens the timeline every 10 min. Pass: API p95 at most 400 ms, reader page p95 at most 300 ms, 5xx under 0.1%, 503 `busy` under 0.5%, loop lag p99 at most 100 ms.
- **Soak:** 2 h, including one worker deploy. Pass: no job fails, and bundles resume.
Record every run's numbers in `docs/ops/capacity.md`.

---

## Order and dependencies
`Phase 0` (start now, it has lead times) → **1 → 2** (safety and eyes) → **3, 4, 5** (can run in parallel: three owners) → **6 → 7** → **8 → 9 → 10, 11, 12, 13, 14, 15** (any order) → **16** → **18 (first load run)** → **17** → **18 again**.
Expected verdicts: after 1–7, 5–10 bundles plus 100 online pass. After 8–16, 300 online passes. After 17 plus quotas, 50 bundles pass.

## Not verified (the implementer must check, not assume)
- The real Bedrock and Azure quotas, Railway memory limits, and the Supabase pooler pool size today.
- psycopg_pool defaults (`timeout` 30 s, `max_waiting` 0): check in the installed version.
- Whether Railway supports a draining setting in `worker.json`.
- Measured Bedrock call latency (spike 5 has no result) and real worker RSS on a 900-page run.

## Verification (overall)
- Each PR: tests first, then `make test-slice S=<slice>`, `api/tests/guards/`, `make lint`, `python scripts/check_spec.py`, a `reviewer` pass, CI green, merge.
- The system: PR 18's load test passes all four scenarios on staging, with results in `docs/ops/capacity.md`.
- First action after approval: write this plan to `docs/ops/scalability-plan.md` on branch `docs/scalability-plan` (docs only), PR, merge.
