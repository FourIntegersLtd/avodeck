# 15. Background jobs, queues, workers and asynchronous processing

Assessment date: 12 November 2026. Branch: `docs/audit-15-background-processing`. Scope: every way Avodeck does work outside a request: the job queue, the workers, the nightly schedules, the housekeeping loop, after-commit tasks, in-process events, database notifications, status streams and the one inbound callback.

## Summary

- **No path was found by which one person can create, read, retry, cancel or influence a job on a case they cannot reach.** Tampered, replayed and mismatched queue rows were refused or ignored in every case tested except the two noted below.
- **The worker and the nightly schedule process never switch on log scrubbing or error reporting** (BG-01, BG-02). No patient text was found in worker logs in any test, because handlers log exception types only. But the safety net that rule 6 relies on is absent in the two processes that handle the most patient text, and a job that fails for good raises no alert.
- **Two replay weaknesses in the worker** (BG-05, BG-06): a second queue row for a job that is still queued or running runs it a second time, and can restart a job that was paused to wait for a person (for example by the cost guard). Both need a write to the queue tables, which only the worker's database role has.
- **No job has an overall time limit** (BG-12). A handler that hangs while its worker stays alive is never retried and holds its case's lock until the worker restarts.
- **Limits on job creation are thin** (BG-07, BG-08, BG-11): the only brake on repeated "process" and "retry" requests is the general request limit of 600 a minute per person for each route, and the queue is first in, first out, so one case's backlog delays everyone else's ordinary work.
- Smaller items: three database-layer gaps not reachable through the API (BG-03, BG-04, BG-09), the worker role's unscoped rights over queue rows (BG-10), queue rows that are never pruned and have no index for the worker's own lookups (BG-13, BG-14), no dependency scanning (BG-15), and two documentation claims the repository does not support (BG-17).
- This audit changed no product code. The fixes are on the same branch (see "Fixed on this branch" under Remediation). Each of the 13 tested findings has a test that fails today and names the finding; the fix PR removes the marker. BG-10 is recorded by a passing test of current behaviour, and BG-15, BG-16 and BG-17 were found by reading.

## How this was tested

- 121 tests in `api/tests/security/test_foundation_background_jobs_*.py` (worker logging, access, replay, state, abuse), with helpers in `api/tests/support/background_jobs.py`. Result when written: **104 passed, 17 expected failures** covering 13 findings. After the fixes, with 22 more tests in `test_foundation_background_jobs_fixes.py`: **140 passed, 3 expected failures** (BG-04, BG-07 and the BG-11 ordering test, each left for its owner or a later change). After the pipeline fixes (BG-04, BG-07, BG-08): the BG-04 test passes (marker removed), 3 tests in `test_pipeline_retry_cap.py`, 11 in `api/tests/repository/test_pipeline_live_job.py` and 7 route tests in `api/tests/http/test_pipeline_process_dedupe.py` (CI, needs Auth) are added. Second foundation round: the BG-07 loop stops at the first refusal and the BG-11 ordering test passes (both markers removed, so no expected failures remain in the audit-15 files); 12 tests in `test_foundation_background_jobs_fairness.py` (BG-11), 22 in `api/tests/repository/test_foundation_deferred_work.py` and 2 in `test_foundation_served_event_in_transaction.py` (BG-16) are added. After the second reviewer pass: 2 more in `test_foundation_background_jobs_fixes.py` (BG-12) and 4 in `api/tests/unit/test_foundation_worker_cell_key.py` (BG-16). Result: **157 passed** in `tests/security/test_foundation_background_jobs_*.py`; with the deferred-work, cell-key and virus-scan tests, **191 passed**.
- Every test asserts the safe behaviour. A weakness is a strict expected failure carrying its id (`BG-nn`); it turns into an error the moment the weakness is fixed, so the marker cannot be left behind. Every "refused" test has a control where the legitimate action on the same path succeeds. Each expected-failure test holds one assertion about the product; its control is a separate passing test, and set-up problems or timeouts raise a different error, so a broken fixture cannot pass as a confirmed weakness.
- Jobs are synthetic kinds on an isolated registry and queue, on seeded cases, run by a real worker against a local Postgres (port 58422) as the real `app_api` and `app_worker` roles. A canary string stands in for patient text. No cloud service, production system or real record was contacted.
- Tests that audits 11, 12 and 14 already hold were not repeated; they are cited where relied on.
- Each claim below is marked **T** (a test), **R** (code or migration read, with the line) or **U** (unverified).
- The rule guard suite (`make -C api guards`) could not be run locally: the isolated database stack has no Auth service, which the HTTP guards need. CI runs it on the PR.

## 1. Verified architecture

### What exists

| Mechanism | Where | Durable | Notes |
|---|---|---|---|
| Job queue | Procrastinate 3.10 on Postgres (schema `procrastinate`), wrapped by `app.jobs` and `api/app/core/jobs.py` | Yes | The only route for long work (R) |
| Workers | `python -m app.worker.main`; Railway services `worker` (`WORKER_QUEUES=bulk`) and `worker-interactive` (`interactive`, concurrency 8) | n/a | One replica each, restart on failure up to 50 times (`railway/worker.json`, `railway/worker-interactive.json`) |
| Nightly schedules | `python -m app.worker.schedules`, Railway cron `15 2 * * *` (`railway/cron.json`) | Yes: `app.scheduled_runs`, one run per date | Also run from the worker's housekeeping loop |
| Housekeeping loop | Inside each worker: stall requeue, orphan sweep, registered sweeps, schedules (`jobs.py:436-543`) | n/a | |
| After-commit tasks | FastAPI `BackgroundTasks` on five routes (emails, one event, one file removal) | **No** | Lost if the API restarts between commit and task (BG-16) |
| In-process events | `DocumentUploaded` and others, handled inside the publisher's transaction (`app/core/contracts/events.py`) | Yes, with the transaction | |
| Database notifications | `LISTEN/NOTIFY` on `case_jobs` and `case_key_destroyed` | No | Only a wake-up signal; every read is re-done under row-level security |
| Status streams | `GET /v1/jobs/{id}`, `/v1/jobs/{id}/events`, `/v1/cases/{case_id}/events` | n/a | Access re-checked at least every 60 seconds (`app/core/routes/jobs.py:107-126`) |
| Inbound callback | E-sign webhook, signed (`app/features/trial/routes_webhook.py`) | n/a | Covered by audit 11 (L6) |

### The job itself

| Aspect | Verified behaviour |
|---|---|
| Producers | About 40 call sites call `registry.enqueue(conn, case_id, kind)` inside the request's transaction (R). 21 kinds and schedules are registered with decorators; the pipeline, reader and close slices register theirs through their own wiring. |
| Enqueue check | Inside the database function `app.enqueue_job` (`supabase/migrations/0015_foundation_job_kind_actions.sql:31-59`): the caller must hold the action registered for that kind on that case, or be the worker already inside a job for the same case. Otherwise error 42501 (T). |
| Payload | `{job_id, case_id}` and nothing else. Kind, status and progress live in `app.jobs` (T: no canary in any queue column). |
| Identifiers | Random UUIDs. |
| Queues | `interactive` (priority 10), `bulk` (priority 0), and the older `jobs` queue drained by the bulk worker (`app/worker/main.py:37-49`). |
| One at a time per case | Kinds marked `changes_case` take the lock `case:<id>`; the queue allows one running job per lock (T). |
| Retry | 3 attempts, 5 seconds apart (`jobs.py:370-383`). Pauses for a busy or unavailable reading service resume themselves with back-off. A stalled job is requeued at most `JOB_MAX_STALL_REQUEUES` times (default 5), then fails with `stalled_too_often` (T, existing tests). |
| Dead letter | There is no separate queue. A job that fails for good stays `failed` with an error code and sends one error-reporting event `job_dead_lettered` carrying only the kind and job id (`jobs.py:352-358`). See BG-02. |
| Person-driven retry | `POST /v1/cases/{id}/process/retry` → `app.requeue_failed_job` (`0120_pipeline_retry_failed_job.sql:10-39`); support staff may resume or requeue through `/v1/support/jobs/{id}/…`. |
| Cancellation | Only `POST /v1/cases/{id}/process/cancel`: it sets a flag that the pipeline checks between steps; the job ends `paused` with `cancelled`. There is no general cancel. |
| Status | `app.jobs` (status, step, percentage, time left, error code) and `app.job_steps`. Never patient text (T). |
| Failure handling | Only the exception's type is logged (`jobs.py:545-626`). Finished steps are skipped on the next attempt. |
| Worker identity | Database role `app_worker` on the direct connection. Each job runs in a transaction scoped to the case id in the queue row; row-level security limits it to that case. The pool refuses to open as a superuser or a role that bypasses row-level security (`app/core/db.py`). |
| Shutdown | On a stop signal the worker waits `WORKER_SHUTDOWN_GRACE_SECONDS`, then cancels; a cancelled job becomes `paused` / `resuming` and continues without using an attempt (T, existing tests). |

### A bundle from upload to done (R)

1. Browser sends parts, then `POST /v1/cases/{id}/uploads/{upload}/complete`. The route claims the upload and enqueues `finalize_upload` (`app/core/routes/uploads.py:370,386`), answering 202 with the job id.
2. The interactive worker picks up the queue row and runs `_run_job` (`jobs.py:545`): opens a transaction scoped to the case, stops if the job is already `done` or the case is on hold, marks it `running`.
3. `finalize_upload` re-reads, decrypts and hashes every part (`app/core/uploads.py:214`). In one transaction it creates the document, marks the upload complete, writes the audit row and publishes `DocumentUploaded` (`uploads.py:204`).
4. The pipeline's subscriber `queue_scan` runs in that same transaction and enqueues `scan_document` (`app/features/pipeline/__init__.py:72-78`). The worker may enqueue because it is inside a job for that case.
5. The virus scan runs. On a clean result the pipeline enqueues `process_bundle` (`app/features/pipeline/service.py:150`), which takes the case lock.
6. The bulk worker runs the steps (split, render, OCR, identifier stripping, extraction, dates, duplicates, gaps, index), recording each in `app.job_steps` and progress in `app.jobs`. With page batching on, the parent parks and child `read_page_batch` jobs do the reading (ADR 0038).
7. Success sets `done`, 100%. The browser has followed all of this on the status stream, never by asking the worker.

## 2. State model

`app.jobs.status` is guarded by the trigger `app.jobs_guard` (`supabase/migrations/0002…:62-84`). Allowed moves, exactly:

```
queued → running → done
            ├────→ failed → queued      (retry)
            └────→ paused → running     (resume)
```

- All 25 pairs were tried as the worker role and as the database owner: only these six change a status, and a row cannot be created in any status but `queued` (T). An update that leaves the status as it is (running to running) is not a move and the trigger lets it through (`0002…:74`); BG-05 depends on exactly that. This matches `docs/spec/state-machines.md:111-127`.
- A queue row is a separate thing from the job row. The trigger guards the job row; nothing ties the number of queue rows to it. That gap is what BG-05 and BG-06 use.
- Cancel against a finished run is refused and leaves no flag; a flag left from an earlier run does not cancel the next one (T; `app/features/pipeline/repo.py:1490,1552`).

## 3. Access control and isolation

| Question | Answer | Basis |
|---|---|---|
| Create a job on someone else's case? | No: 42501 from the database function; 404 from the routes | T |
| Guess or swap a job id to read status or the stream? | No: 404, same as an unknown id. `/v1/jobs/{id}` sits outside the `/v1/cases/{…}` scan that enforces rule 9, but checks case access itself (`routes/jobs.py:64-75`) | T |
| Retry or cancel another person's processing? | No: 404 for an outsider, 403 for a viewer; an assistant succeeds (control) | T |
| A job reaching data outside its case? | No: the worker's transaction sees only the case in its queue row | T, and audit 11 |
| Queue row naming one case's job and another case's id? | Handler does not run; neither case changes | T |
| Queue row with an unknown job id, or for a deleted case? | Ends quietly, nothing written | T |
| Queue row for an unknown kind? | Job fails with `unknown_kind` | T |
| Replay of a finished or failed job's queue row? | Ignored | T |
| Replay for a job still queued, running or paused? | **Runs again** | BG-05, BG-06 |
| API database role writing queue tables directly? | No rights on the `procrastinate` schema at all | T |
| API database role inserting a bare `app.jobs` row? | **Yes, for its own cases, with any kind**; the orphan sweep then queues it | BG-03 |
| Another person's temporary files? | Each job writes under a per-job directory; see audit 12 for the files written outside it | audit 12 |

**Permission at run time.** The check happens once, at enqueue. A job still runs after the person who asked for it is removed from the case (T, recorded as current behaviour). The worker does check, at run time, that the case still exists and is not on hold. `export_case` and the transcript export only check that a requester was recorded (`app/features/close/jobs.py:380-386`, `app/features/reader/transcript_jobs.py:73-76`); the finished file is then fetched through a route that checks access and step-up again, so a removed person cannot collect it. This is listed as a policy item rather than a finding.

**The worker role.** Inside the `procrastinate` schema `app_worker` can read, change, insert and delete every case's queue rows (T, BG-10). Row-level security on `app` tables still confines each job to its case, and queue rows hold ids only, so this gives a compromised worker no patient data it does not already reach job by job. It does let one compromised handler start, stop or duplicate any case's jobs.

## 4. Integrity and concurrency

Holds (T unless marked):

- Two case-changing jobs on one case never overlap; jobs on two cases do run side by side.
- A handler that fails every time runs exactly 3 times and stays `failed`.
- Finished steps are not repeated on retry; a crashed worker's job is requeued and continues (existing guard `test_foundation_job_failure_modes.py`).
- The orphan sweep re-queues a `running` job with no queue row once, not twice.
- Enqueue is in the caller's transaction: a rolled-back request leaves no job and no queue row (R).

Does not hold:

| Id | Finding |
|---|---|
| BG-05 | Two queue rows for one job that takes no case lock, picked up by two workers, run the handler twice. `_run_job` skips only a job that is already `done`; marking a `running` job `running` again passes the guard (`jobs.py:545-626`). |
| BG-06 | A queue row for a job paused for a reason that waits for a person (`cost_guard`, `cancelled`) restarts it. The worker does not ask whether the pause is one it may resume. |
| BG-12 | No overall time limit. The stall timeout only catches a dead worker; a handler awaiting for ever keeps its heartbeat (`jobs.py:372-434`). |
| BG-16 | After-commit tasks are not durable: a served-report event or an invoice email is lost if the API process stops between commit and task (R; `app/features/report/routes_serve.py:142`, `routes_invoices.py:394`). |

Storage and database can disagree after a killed worker (a named plaintext temporary file, a sealed object with no row); audit 12 covers this and it is not repeated here.

## 5. Resource and cost abuse

| Id | Finding | Basis |
|---|---|---|
| BG-08 | Each `POST /v1/cases/{id}/process` queues a new job; 200 requests queue 200 jobs. The case lock makes them run one after another, each repeating work the steps mostly skip, but each holds a worker slot. | T (`app/features/pipeline/router.py:675`) |
| BG-07 | Each person-driven retry sets attempts back to 0, so an always-failing job can be run 3 more times per click, without end. | T |
| BG-11 | Within a priority the queue is first in, first out. One person's backlog on many cases delays another person's ordinary job until it drains. Interactive work is on its own worker and is not held up (control passes). | T |
| BG-09 | `app.enqueue_job` takes the queue name and priority from its caller. The API always passes the registered ones; a SQL-level caller could jump the queue. | T |
| BG-13 | The orphan sweep and the run lookup search `procrastinate_jobs` by `args->>'job_id'`, which has no index. The missing index is tested. On a 25,000-row backlog the first sweep took about 10 seconds: seen once by hand during this audit, not a repeatable measurement. | T (index), U (timing) |
| BG-14 | Nothing deletes finished queue rows or their events. | T |

What does limit cost today: the general request limit (600 a minute per person for each route, `app/core/config.py:215`, `app/core/http.py:301`); the per-person AI budget and the upload budget added by audit 14; the cost guard that pauses a job; worker concurrency (4 bulk, 8 interactive); the 3-attempt cap; the stall cap. Audit 14 bounds model calls per job. Whether job retries multiply provider calls during a real outage remains unverified, as audit 14 recorded.

`docs/security/threat-model.md` T15 lists "per-expert quotas, queue limits" as the control for "many jobs". No limit on queued jobs per person or per case exists in the code (BG-17).

## 6. Sensitive data

| Place | Finding | Basis |
|---|---|---|
| Queue payload and events | Job id and case id only | T |
| `app.jobs`, `app.job_steps` | Codes and numbers only, after a handler raised with the canary in its message, including a database error whose detail carried the canary | T |
| Worker log output at DEBUG, with tracebacks, for a failing handler, sweep and schedule | Canary absent | T |
| Dead-letter event | Kind and job id only | existing test |
| Log scrubbing in the worker | **Not installed** | BG-01 |
| Error reporting in the worker | **Not started**, so its scrubbing filter is moot and no alert is sent | BG-02 |

`logs.configure()` and `logs.init_sentry()` are called only from `api/app/main.py:128,198`. `app/worker/main.py` and `app/worker/schedules.py` call neither. Today's clean result therefore rests on every handler and every library logging carefully. One careless `logger.exception` in a slice, or a library that logs its arguments, would put record text into Railway logs with nothing to catch it. The existing dead-letter test starts error reporting itself, which is why it passes.

**Access and retention of job records.** `app.jobs` and `app.job_steps` follow the case: readable by people with access to the case, deleted with it. Queue rows do not: they are never pruned (BG-14) and outlive the case (audit 12, D1f). They hold a case id and timestamps, which after deletion identify nothing in the database but do show that a case existed and when it was worked on.

## 7. Worker security

| Aspect | Finding | Basis |
|---|---|---|
| Database rights | Restricted role, per-job case scope, never the service role | T, audit 11 |
| Queue rights | Unscoped across cases | BG-10 |
| Secrets | `api/.env.example` gives one set of names for the API and the worker. Nothing in the repository gives the worker fewer secrets than the API (for example, it does not need the Auth service key or the session secrets). | R; deployed values U |
| Network | The threat model (T3) says the worker "has no outbound network except allow-listed APIs". The only allow-list in the repository is the AI client's endpoint check (`app/core/config.py:515`). Nothing in `railway/*.json` or `api/Dockerfile` restricts egress. | R; BG-17 |
| File system | Per-job scratch directories; gaps recorded in audit 12 | audit 12 |
| Isolation between jobs | Jobs share one process and one memory space. Separation is by database scope, not by process. A handler bug that keeps state in a module-level variable could carry data from one case's job into the next. No such variable was looked for exhaustively. | R, U |
| Hostile files | Parser limits and exhaustion are in audit 12 | audit 12 |
| Dependencies | No vulnerability scanning: no Dependabot file, and no audit step in `ci-api.yml`, `ci-web.yml`, `deploy-migrations.yml` or `eval-nightly.yml` | R; BG-15 |
| Restart | Restart on failure up to 50 times; the database wait added on 8 November keeps a short outage from using the budget up (`app/worker/main.py:93-121`). After 50 failures the service stays down and, with BG-02, nothing reports it from inside the worker. | R |

## 8. Findings register

| Id | Severity | Finding | Evidence | Verified | Fix | Owner |
|---|---|---|---|---|---|---|
| BG-01 | Medium | Worker and schedule processes do not install log scrubbing | `app/worker/main.py:156-157`, `app/worker/schedules.py`; only `app/main.py:198` calls it | T | Call `logs.configure()` at the start of both `main()` | foundation (Fixed: `start_observability` in `app/worker/main.py`) |
| BG-02 | Medium | Worker and schedule processes do not start error reporting: no dead-letter or schedule-failure alert | same; only `app/main.py:128` | T | Call `logs.init_sentry()` in both | foundation (Fixed: same) |
| BG-05 | Medium | A duplicate queue row runs an unfinished job twice | `app/core/jobs.py:545-626` | T | Claim the job with a status condition; skip when already `running` and not a stall requeue | foundation (Fixed: conditional claim in `app/core/jobs.py`) |
| BG-06 | Medium | A stray queue row restarts a job paused to wait for a person | same | T | Refuse a paused job whose reason is not self-resumable | foundation (Fixed: same) |
| BG-12 | Medium | No overall time limit on a job | `app/core/jobs.py:372-434` | T (no limit exists); hang not timed | Per-kind time limit around the handler | foundation (Fixed: per-kind time limit, `timed_out`) |
| BG-08 | Low–medium | Repeated "process" requests each queue a job | `app/features/pipeline/router.py:675` | T | Return the live job when one is queued or running | pipeline. **Fixed on this branch** (live job returned; the BG-11 cap stays as the backstop) |
| BG-11 | Low–medium | No fairness between cases or people within a queue | queue order | T | Cap queued jobs per case, or order fairly | foundation. **Fixed on this branch** (cap, migration 0070; ordering, migration 0072) |
| BG-13 | Medium (operational) | No index for lookups by job id in queue rows | `0138_pipeline_orphan_sweep_skips_parked.sql:12-65` | T | Expression index on `args->>'job_id'` | foundation (Fixed: migration 0069) |
| BG-03 | Low | API role can insert a job row directly, skipping the per-kind check; the orphan sweep queues it | `0003_foundation_case_access.sql:196-201` | T | Remove the insert right and the `jobs_add` rule | foundation (Fixed: migration 0068) |
| BG-04 | Low | `requeue_failed_job` checks the upload action for every kind | `0120_pipeline_retry_failed_job.sql:10-39` | T | Check the action registered for the job's kind | pipeline. **Fixed on this branch** (migration 0144) |
| BG-07 | Low | Person-driven retry has no lifetime cap | same | T | Count retries per job; cap them | pipeline. **Fixed on this branch** (migration 0144) |
| BG-09 | Low | Enqueue function trusts the caller's queue and priority | `0015_foundation_job_kind_actions.sql:31-59` | T | Take both from the kind's registration | foundation (Fixed: migration 0070 (9006 flags)) |
| BG-10 | Low | Worker role has unscoped rights over all queue rows | `0007_foundation_files_jobs.sql:606-610` | T | Narrow to what the worker's functions need, or accept and record | foundation (Accepted: no change) |
| BG-14 | Low | Finished queue rows and events are never pruned | `app/core/jobs.py:416-434,465-495` | T | Daily prune of finished rows older than a set age | foundation (with close for deleted cases) (Fixed: migrations 0071, 9007) |
| BG-15 | Low | No dependency vulnerability scanning | `.github/workflows/` | R | `pip-audit` and `npm audit` in CI, plus Dependabot | foundation (Fixed: `.github/dependabot.yml`, report-only audit steps) |
| BG-16 | Low | After-commit tasks are lost on an API restart | routes listed in section 4 | R | Move the served-report event and emails to jobs or an outbox | foundation (mechanism built, migration 0073) and report, organisations, reader (call sites). **Fixed on this branch** (mechanism and every call site) |
| BG-17 | Info | Threat model claims worker egress limits (T3) and queue limits (T15) that the repository does not show | `docs/security/threat-model.md:22,34` | R | Build them or correct the document | foundation (Fixed: threat model T3, T15 corrected) |

## Policy items, with the default taken

Nothing was changed; these describe today's behaviour for a decision.

1. **A job runs after its requester loses access.** The work belongs to the case, not the person, and its result is only readable by people who still have access. Left as is.
2. **Export requested by someone since removed** still builds the file; collecting it needs access and step-up. Left as is. A stricter rule would re-check the requester when the export job starts.
3. **Retry resets the attempt count** (BG-07). Deliberate, so that "Try again" gives a full set of attempts. A lifetime cap would bound it.
4. **Jobs share a process.** Separate processes per job would cost throughput; database scoping is the boundary relied on.

## Unverified

| Item | Evidence needed |
|---|---|
| Which secrets the deployed worker services hold, and any network rules on them | Railway service settings, names only |
| That worker logs in Railway are in fact unscrubbed, and whether an error-reporting address is set for the worker | A deployed worker's start-up, values not printed |
| How a real hung handler behaves over hours | A timed run on a staging worker |
| Whether job retries multiply provider calls in a real outage | As audit 14 |
| E-sign callback replay (same signed body sent twice) | A test in the trial slice; audit 11 L6 covers the trust placed in it |
| Module-level state shared between jobs in slice handlers | A sweep of each slice's `jobs.py` |
| The rule guard suite against these tests | CI on the PR |

## Remediation, in priority order

1. **BG-01, BG-02:** switch on log scrubbing and error reporting in `app/worker/main.py` and `app/worker/schedules.py`. Two lines each; the tests are already written.
2. **BG-05, BG-06:** make the worker's claim of a job conditional on its status and pause reason.
3. **BG-12:** per-kind time limit.
4. **BG-08, BG-11:** one live "process" job per case; a cap on queued jobs per case.
5. **BG-13, BG-14:** index and prune the queue tables; include queue rows in case deletion (closes audit 12 D1f).
6. **BG-03, BG-04, BG-09, BG-10:** tighten the database layer.
7. **BG-15:** dependency scanning in CI.
8. **BG-16, BG-17, BG-07:** durable after-commit work; correct or build the threat-model claims; retry cap.

### Fixed on this branch

- **BG-01, BG-02:** `start_observability` in `app/worker/main.py`, called by the worker and the schedule process; tests in `test_foundation_background_jobs_worker_logging.py` (markers removed).
- **BG-05, BG-06:** the worker claims a job only when it is `queued`, `running` with `requeued_stall`, or `paused` with `service_busy`, `model_unavailable`, `resuming` or `busy_processing`; any other queue row is skipped. Support's resume now sets `resuming` (migration 9008) so it is still a legitimate resume. Failure mode F105.
- **BG-12:** per-kind time limit, a backstop for a hung job and not a deadline for a slow one (bulk 6 h, interactive 1 h; `JOB_TIME_LIMIT_*_SECONDS`; first set at 2 h and 15 min, raised after the second reviewer pass), ordinary failure path, then `failed` with `timed_out`, case lock released. Failure mode F103; `state-machines.md`.
- **BG-03:** migration 0068 drops the `jobs_add` policy and revokes insert on `app.jobs` from `app_api`; jobs come only from `app.enqueue_job`.
- **BG-09:** migration 0070 pins priority to 0 or 10, and queue and priority by the kind's `interactive` flag (backfilled in 9006). A slice that adds a job kind must add its `app.job_kind_actions` row with `interactive` (and `max_active` if above 5); a test fails if a registered kind has none. **Accepted:** the queue pin and the fairness exemption rest on the `app.job_case_id` setting, which `app_api` could set for itself at SQL level. That is the same trust as `app.person_id` (the API role is trusted to set its own scope, ADR 0013) and no worse than before this fix, so it is accepted, not closed.
- **BG-11:** migration 0070 caps queued-or-running jobs per case and kind at 5 (SQLSTATE `AVCAP`, 429 `too_many_jobs`, `Retry-After: 30`), exempt for the worker inside its own job. Failure mode F104. Ordering: migration 0072 adds a before-insert trigger on the queue table that lowers a new row's priority by the number of rows its case already has queued or running on the queue, at most 9 (bulk 0 to -9, interactive 10 to 1, so interactive stays above bulk and BG-09's pin by kind holds). It is a trigger and not a change inside `enqueue_job` because the queue function `defer` bypasses `enqueue_job`, and the retry function, orphan sweep and stall requeue also make rows. Not lowered: a worker inside its own job's case (page-batch children, ADR 0038), a job that already had a queue row (the heal sweep's hand-back, a retry), and any priority other than 0 or 10 (the parent's wake at 20). `app.jobs.priority` keeps the base value. Tests: `test_foundation_background_jobs_fairness.py`; the ordering test's marker is removed. Failure mode F108. **Accepted:** the exemption for the worker inside its own job relies on `app.job_case_id`, which `app_api` could set for itself at SQL level; the same trust as `app.person_id` (ADR 0013), no worse than before.
- **BG-08:** the cap bounds the flood (at most 5 queued "process" jobs per case); the dedupe is done by the pipeline owner (below). The marker was removed because the test passes; it needs Auth and runs in CI.
- **BG-04 (pipeline):** migration 0144 replaces `app.requeue_failed_job`: only the four processing kinds, and the action registered for the kind in `app.job_kind_actions`. Test: `test_requeue_failed_job_needs_the_action_of_the_kind` (marker removed) and `test_pipeline_retry_cap.py::test_a_kind_the_route_does_not_retry_is_refused_even_for_a_person_who_may_run_it`.
- **BG-07 (pipeline):** migration 0144 counts a person's retries per job in `app.pipeline_job_retries` and refuses the 6th (the route answers 409 `retry_limit`; screen 07 says to contact support). Support's requeue is not capped. Test: `test_pipeline_retry_cap.py::test_the_sixth_person_retry_of_one_job_is_refused_and_flagged`, `test_pipeline_process_dedupe.py::test_retry_answers_409_retry_limit_once_the_job_used_its_retries`. Failure mode F107. The foundation test `test_a_failed_job_cannot_be_put_back_without_end` now stops at the first refusal (the 7th lap used to set the still-failed job to `running`, which the job guard refuses, so the test raised before its assertion); the assertion that a refusal comes before lap 8 is unchanged and the marker is removed.
- **BG-08 (pipeline):** `POST /v1/cases/{id}/process` returns the live `process_bundle` job (queued, running with a recent heartbeat, or paused to resume) with the same 202 shape; a new one is queued only when none is live. Test: `test_pipeline_process_dedupe.py`; the cap stays as the backstop.
- **BG-13:** migration 0069, index on `args ->> 'job_id'`.
- **BG-14:** migration 0071 `app.prune_queue_rows` (finished rows and events older than 30 days, `QUEUE_RETENTION_DAYS`, and any finished row of a deleted case; `app_worker` only), called by the worker's daily housekeeping; migration 9007 makes `purge_case` remove the case's queue rows. **This closes audit 12 D1f**, apart from the delete job's own row, which the daily prune removes. Failure mode F106.
- **BG-10: accepted.** Procrastinate's functions run as the caller and need insert and update on its tables, so narrowing the worker's rights breaks on upgrades and a compromised worker could still change rows. The documenting test is unchanged. The prune is a security definer function, so delete could be revoked later.
- **BG-15:** `.github/dependabot.yml` (pip `api/`, npm `web/`, actions), a `dependency-audit` job in `ci-api.yml` and a `pnpm audit` step in `ci-web.yml`, both report-only. First runs (12 November): `pip-audit` found 26 advisories in certifi, cryptography, filelock and setuptools; fixed by `poetry update` to certifi 2026.10.23, cryptography 50.0.2, filelock 4.2.10, setuptools 84.2.1 (crypto, auth-token, TOTP and guard tests green). `pnpm audit --audit-level=high`: 1 high and 3 critical, all development tools only (vitest below 3.2.6, tinypool below 2.1.2, vite below 6.4.4 via vitest and the React plugin); the production dependencies show 2 moderate. Not bumped: vitest 3 and vite 6 are major versions; Dependabot will open them.
- **BG-17:** `docs/security/threat-model.md` T3 and T15 now describe the queue cap, time limit, priority pinning and prune. Worker egress restriction is **not configured in this repository** and must be set in Railway by hand; it remains open.
- **BG-16 (mechanism):** migration 0073 adds `app.deferred_work`, a sealed outbox written inside the request's transaction (`app/core/deferred.py`: `defer_mail`, `defer_call`) and delivered by a worker sweep with retry (backoff 30 s doubling to 1 h, failed after 8 tries, expired after its validity, at most 7 days), at least once. The payload is sealed with the cell key (`SecretBox`, bound to the row) and wiped when the row finishes, so no link, address or secret sits in plaintext; the table has no grants to `app_api` or `app_worker`, only definer functions; email retries carry the row id as the Resend `Idempotency-Key`, so a repeat does not send twice. At most 500 pending rows per person (`AVCAP`). The served-report event needs no new mechanism: `EventBus.publish` already runs subscribers inside the request's transaction (test shows a subscriber's write commits or rolls back with the request); the report route should call it instead of a background task. Failure mode F109. `tests/support/deferred.py::drain` delivers once for tests.
- **BG-16 (call sites):** the serve route publishes the served event inside its transaction (`report/routes_serve.py`) and the after-serving handler writes its deadlines on that connection (`after_serving/events.py`), so they commit or roll back with the serve; invoice and spot-check emails (`report/routes_invoices.py`, `routes_spot_checks.py`, the code's lifetime as validity), the support-account email (`organisations/accounts.py`, the link's lifetime as validity; it carries a link, never a password) and transcript file removal (`reader/transcript_jobs.py`, `reader.transcript_remove`, safe to run twice) go through deferred work. No FastAPI `BackgroundTasks` use remains in `api/app`. Tests: `test_report_sign.py`, `test_report_invoices.py`, `test_report_spot_checks.py`, `test_after_serving_served_by.py`, `test_organisations_support_accounts_routes.py`, `test_reader_transcript_job.py` (need Auth, run in CI).
- **Pipeline follow-up:** the two retry helper functions in migration 0144 are definer functions with a pinned search path and no execute grant, as the pipeline's own test requires of every pipeline function.
- **Left open:** worker egress restriction (BG-17), which is set by hand in Railway.

## Reviewer pass

An independent reviewer read the report and every test against the cited code, without running them. It confirmed every entry in the register against the code and found that no later migration undoes a finding. It raised nine points, all on the report's wording or the tests' design; none changed a finding.

| # | Point | Outcome |
|---|---|---|
| 1 | The BG-03 sweep test committed a database-wide sweep, leaving queue rows for other tests | Fixed: the sweep runs in a transaction that is rolled back and checks its own job only |
| 2 | In six expected-failure tests a failing control or a timeout could have counted as the expected failure | Fixed: controls moved to five separate passing tests; set-up and timeouts raise a different error |
| 3 | The BG-14 test looked only for a database function, so a fix in Python would not have cleared it | Fixed: it now ages a finished queue row and checks it is gone after a housekeeping round |
| 4 | The 10-second figure for BG-13 was marked as tested; the index check would pass for any index on the payload | Fixed: figure marked as seen once by hand; the check requires an index on the job id |
| 5 | "Each finding has a test that fails today" was not true of BG-10, BG-15, BG-16, BG-17 | Fixed in the summary |
| 6 | "Only these six succeed" left out same-status updates, which BG-05 depends on | Fixed; a test now records it |
| 7 | The request limit is per person for each route, not per person | Fixed |
| 8 | BG-05 wording differed between the test and the report | Aligned |
| 9 | The error-reporting test fixture did not restore the earlier client | Fixed; integrations installed by the reset are not restored |

After the fixes the tests ran twice with the same result: 104 passed, 17 expected failures.

## Second reviewer pass

A second independent review of the branch raised these. All are fixed, none changed a finding.

| # | Point | Outcome |
|---|---|---|
| 1 | The time limits (2 hours bulk, 15 minutes interactive, no kind setting its own) would stop legitimate long jobs (`export_case`, `build_trial_pack`, `match_trial_bundle`, `finalize_upload`, `reprocess_case`, `delete_case`, `process_bundle` on 4,000 pages), and a timed-out job reruns | Fixed (foundation): the defaults are a backstop for a hung job, 6 hours bulk and 1 hour interactive, still set from `JOB_TIME_LIMIT_*_SECONDS` and per kind. Tests show a retry after a timeout keeps the steps that finished and that a job timing out on every attempt ends `failed` / `timed_out` once and is not run again. `state-machines.md`, F103, the threat model and `.env.example` updated |
| 2 | One sweep transaction sent up to 20 mails in turn holding their row locks, and ran first, so a mail outage delayed the stall, orphan and pipeline-heal sweeps by minutes | Fixed (foundation): delivery is a database sweep that runs after the others, claims one row per short transaction (skip-locked), gives each row at most 30 seconds and a round at most 20 seconds. Tests: a failing or hung mail service leaves the other sweeps running in the round, two sweeps at once never send a row twice. F109 updated |
| 3 | The worker needs the cell key for deferred work and was never required to have it | Fixed (foundation): the worker stops at start-up with a `ConfigError` naming `KEY_PROVIDER` or `LOCAL_MASTER_KEY` (never the value). Listed for the worker in `docs/runbooks/railway.md` and `api/.env.example` |
| 4 | The cap and fairness exemption rely on `app.job_case_id`, which `app_api` could set for itself | Accepted: same trust as `app.person_id` (ADR 0013), no worse than before; recorded under BG-09 and BG-11 above |
| 5 | The support-account email was deferred before the block that removes the new login on failure | Fixed (organisations): it is now deferred inside that block, so a failure removes the login and leaves no mail |
| 6 | `app.requeue_failed_job` took queue and priority from its caller | Fixed (pipeline, migration 0144): queue and priority are pinned to the kind |
