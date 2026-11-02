# API and worker (FastAPI, Python 3.12, Poetry)

Read the root `AGENTS.md` first. This file covers `api/`: the API and the background worker, one Poetry project (`make -C api help` lists the commands). Local secrets live in **`api/.env`** (see `api/.env.example`); `make start` and `make worker` load it.

## Layout

```
api/app/
  main.py                 # app factory, routers, middleware
  core/                   # foundation-owned
    auth/                 # cookie session, Supabase Auth calls, MFA, step-up, CSRF
    db.py                 # async Postgres (psycopg 3 pool), restricted roles, SET LOCAL per request/job (ADR 0013)
    storage.py            # S3 object store for case files (ADR 0032)
    jobs.py               # Postgres job queue, progress, server-sent events
    audit.py              # append-only, hash-chained audit log
    ai/                   # AI kit: pii.py, bedrock.py, ocr.py, provenance.py, cell.py, kit.py, recordings.py
    speech.py             # WebSocket relay to Azure Speech
    contracts/            # shared contracts (only grow): Fact + citable_facts, Question, ReportVersion + ensure_unlocked,
                          # NeedsYouItem + needs_you registry, require_page_read (423 gate), page_read_log +
                          # verification_counts (reading.py), events, case_tables,
                          # profile (named expert's details, template, declarations, signature, conflicts for a case),
                          # ai_calls (ai_call_summary: AI metadata per task for the methodology appendix),
                          # latest_opinions (opinions.py), Citation + citation_problems (citations.py) for F,
                          # case_invoice_rows (invoices.py: F's sent invoices, for H's invoices.csv),
                          # hearing_state (hearings.py), served_answers (written_answers.py) and
                          # add_report_changes (report_changes.py): G's data for I; trial_freshness for I and G;
                          # org_policy (org_policy.py) and request_case_deletion + case_export_done (close.py) for O,
                          # create_replacement_case (replacement.py) for O
    housekeeping.py       # daily schedules owned by foundation (expire_uploads)
    packs.py              # pack loader: merges jurisdiction + case-type + specialty packs for a case
    config.py             # cell config: REGION, endpoints, model IDs, allowed packs
    flags.py              # feature flags
  worker/                 # foundation-owned: job queue consumer (see "Worker" below)
    main.py
  features/<feature>/     # one per slice: pipeline, reader, setup, understand, write, report,
                          # after_serving, close, trial, organisations (.claude/routes.json)
    router.py             # routes for this feature only
    schemas.py            # request/response Pydantic models
    service.py            # business logic
    repo.py               # SQL for this feature's tables only
api/tests/                # grouped by kind; files named test_<feature>_<area>_*.py
  guards/                 # the non-negotiable rules' tests (foundation-owned)
  unit/                   # services and domain logic
  http/                   # API tests through httpx
  repository/             # SQL and RLS against local Supabase
  contract/  security/    # OpenAPI contract, auth and access tests
  fixtures/               # recorded AI/OCR responses, synthetic pages
  support/                # shared test helpers
```

`make test-slice S=<feature>` runs every `api/tests/*/test_<feature>_*.py`.

## Route conventions

- Prefix `/v1`. Case routes are `/v1/cases/{case_id}/…`.
- Build routers with `app.core.http.router()` (signed in, one transaction per request, Idempotency-Key) and add them in `app/main.py` with `mount(app, router)`, never `include_router()`: FastAPI includes routers lazily, which hides their routes from the rule 9 route scan.
- Editable resources return `ETag` (`etag(version)`); updates take `IfMatch` (428 without it, 412 `stale`).
- Every case route depends on `require_case_access("<action>")` (actions in `docs/spec/permissions.md`; 404 if no access, 403 if the role forbids the action). Never write your own access check.
- Risky routes (sign, send, export, delete, retention) depend on `require_step_up` (403 `step_up_required` without recent `aal2`; 403 `mfa_required` with `mfa_enrolled` from a password-only session, ADR 0025).
- State-changing routes depend on `require_csrf`.
- Long work returns `202` + `{"job_id": ...}` and enqueues a job. Never do OCR, AI or document building inside a request.
- Page-content routes that show suggestions depend on `require_page_read()` (`core/contracts/reading.py`): 423 `blind_read` until the person has read the page (rule 3). Slice B provides the read log as `app.state.page_reads`.
- Start-up work: a feature package may define `on_startup(app)` (API, database ready) and `on_worker_start(worker)` (worker); sync or async, run in feature name order (`core/features.py`).
- Today items: register a provider with `@needs_you.provider("<name>")` returning `NeedsYouItem`s (`core/contracts/today.py`); it runs as the signed-in person under RLS.
- A locked `report_versions` row raises SQLSTATE `AVLCK` in the database, returned as 409 `locked`.
- Errors use `ApiError(code, message, status)`. Codes: `not_found`, `blind_read`, `locked`, `step_up_required`, `conflict`, `invalid`.
- Files stream through `core/storage.py`. Responses carrying page images or documents set `Cache-Control: private, no-store`.
- Never return patient text in error messages.

## Auth

- The browser never sees Supabase tokens. `POST /v1/auth/sign-in` calls Supabase Auth server-side and sets an `httpOnly`, `Secure`, `SameSite=Strict` cookie.
- Verify Supabase access tokens against JWKS: check `aud`, `exp`, `sub`, `aal`. Never decode without verifying.
- 30-minute inactivity timeout and a maximum session length. Log sign-in success and failure to `audit_log`.

## Country-neutral code

- Get every legal or clinical rule from `pack = packs.for_case(case)`: `pack.deadline("written_questions", served_on)`, `pack.declarations()`, `pack.term("subject")`, `pack.compliance_checklist()`, `pack.workflow_steps()`, `pack.fact_kinds()`, `pack.lanes()`, `pack.checks()`, `pack.own_record_sources()`, `pack.arriving_kinds()`, `pack.guidelines()`, `pack.date_order`, `pack.identifier_recognisers()`, `pack.party_roles()`, `pack.instructing_party_types()`, `pack.timezone`, `pack.speech()`, `pack.prompt_context()`.
- Never hard-code the forbidden words in architecture 3.5 (rule names, guidance bodies, declarations, party and instructing-party words, clinician titles, record systems, identifier types), a currency, date order, timezone, holiday rule, retention period, or a fixed list of fact kinds, source types, question types, party roles, lanes, checks, deadline kinds or document kinds.
- Store times in UTC; compute deadlines with `pack.deadline()` (calendar and timezone aware).
- Build AI prompts from neutral templates plus `pack.prompt_context()`; generate output schemas from pack registries.
- Write each feature test so it also passes under the fixture packs (`packs/_fixtures/`).
- Store `pack_version` on anything that depends on pack content (served reports, deadlines, checks run).
- Endpoints and model IDs come from `core/config.py` for the current cell; the AI kit refuses anything outside the cell's allow-list.

## Database

- Tables live in the `app` schema, which is not exposed to Supabase's Data API.
- Every case table has `case_id` and an RLS policy using `app.can_access_case(case_id, action)`. FastAPI and the worker connect as restricted roles through `core/db.py`, which sets `app.person_id` (or `app.job_case_id`) per transaction. Never use the service role for data, and never open your own connection (ADR 0013).
- Append-only tables (`page_reads`, `fact_verifications`, `audit_log`) have no UPDATE/DELETE grants and carry `prev_hash`/`hash`.
- Only your feature's `repo.py` writes your slice's tables.

## AI calls

Always go through `core/ai/`. Never call Bedrock or Azure directly from a slice.

```python
from app.core.ai import provenance
from app.core.ai.kit import AiKit, AiTask

TASK = AiTask(
    task_id="T-07b",
    prompt_version="2026-10-16.1",
    model="sonnet",  # cell allow-list resolves the ID
    system=NEUTRAL_TEMPLATE,
    schema=schema_from_pack_registries,
)
out = await kit.run(
    conn, TASK, pack=pack, case_id=case_id, text=page_text, names=case_names, dates_of_birth=case_dobs
)  # pii.strip runs inside run()
accepted, rejected = provenance.check_items(out["items"], ocr_words)  # values not in cited word_ids are rejected
```

- `run()` checks the endpoint and model against the cell allow-list, applies the cost guard (F11), strips identifiers, validates the answer against the schema (`InvalidOutput`), backs off on `ServiceBusy` and raises `ProcessingPaused` (503 `processing_paused` with a `reason`) when the service stays busy, the model is unavailable (F24, no fallback) or the key service is unreachable.
- Live clients: `LiveBedrock` (`core/ai/bedrock_live.py`) and `AzureOcr` (`core/ai/azure_ocr.py`); `core/ai/clients.py` `live_or_recorded()` picks live (keys set), recorded (`AI_RECORDINGS_DIR`) or none. Features call only that factory.
- Stand-ins: `RecordedBedrock`, `RecordedOcr` (`core/ai/ocr.py`) and `RecordedSpeech` (`core/speech.py`) replay recordings in `tests/fixtures/ai/` keyed by a request hash and refuse anything unrecorded.
- `ai_calls` logs task, model ID, region, prompt version, token counts and outcome. It never logs content. Logs are scrubbed by `core/logs.py`; scrubbing is a safety net, not permission to log patient values.
- Per-case data is encrypted with `core/crypto.py` `CaseCrypto` (one key per case, fail closed).

## Testing

- `pytest` + `httpx.AsyncClient`. Local Supabase via `supabase start`.
- Bedrock, Azure OCR and Azure Speech are faked with recorded fixtures in `tests/fixtures/`. Unit and API tests never call real models.
- Each slice keeps a coverage floor of 85% for `service.py` and `router.py`.
- Write API tests from `docs/architecture.md` section 5 before the route exists.
- The `tests/guards/` tests are the product's safety net. If one fails, stop.

## Worker (`app/worker/`: bundle pipeline and background jobs)

The worker is part of this package (`app/worker/`, run with `make -C api worker`). It shares `app/` with the API and runs as a separate App Platform component that consumes the Postgres job queue.

### Job types

| Job | Owner slice | Does |
|---|---|---|
| `process_bundle` | A | Split → render → OCR → strip identifiers → per-page extraction → dates → duplicates → gaps → missing items → index |
| `process_pages` | A | Same, for appended pages only |
| `extract_questions` | C | OCR letter of instruction → T-06 |
| `rerun_checks` | D | Re-run "Things to look at" |
| `suggest_points` | E | Sort verified facts for and against a question (T-14a/T-14b); queued by `opinion.write` |
| `compare_witness_statement` | E | Check a witness statement against verified facts (T-14c); queued by `opinion.write` |
| `build_report` | F | Assemble the report in the expert's Word template |
| `final_check` | F | 28 checks |
| `verify_opposing` | G | T-21b/c/d |
| `match_trial_bundle` | I | OCR (Read) + page matching |
| `build_trial_pack` | I | Large-print PDF |
| `export_case` | H | ZIP of the record of work |
| `delete_case` | H | Scheduled deletion + certificate |

### Registering a job

```python
from app.core.jobs import JobContext, registry


@registry.kind("process_bundle", interactive=False, changes_case=True)  # changes_case: one at a time per case
async def process_bundle(ctx: JobContext) -> None:
    await ctx.step("split", split)  # skipped if it finished on an earlier attempt (F6)
    await ctx.progress(40, eta_seconds=120, step="ocr")


job_id = await registry.enqueue(conn, case_id, "process_bundle")  # in the request's transaction; 202 + job_id
```

Daily work uses `@registry.schedule(name)` with a handler taking `(conn, run_date)`; each date runs once (F28). Handlers raise `ProcessingPaused` to pause; any other exception is retried, then `failed` with `job_error`. Log exception types only.

### Rules

- **Idempotent per step and per page.** A job can be retried safely; every step checks whether its output exists before redoing work.
- **Bounded concurrency** for Azure OCR and Bedrock calls; respect rate limits with back-off.
- **Progress:** update `jobs.pct`, `jobs.step`, `jobs.eta_seconds`. No patient text in job rows or logs.
- **Pages keep their numbers forever.** `bundle_page_no` is set once. Appended records continue from the last number.
- **Duplicates are set aside, never deleted.**
- **Never auto-correct handwriting.** Low-confidence pages go to the "Pages we couldn't read" queue (screen 10).

### AI pipeline rules (from docs/architecture.md section 6)

1. `pii.strip()` before every Claude call, using the case's pack identifier recognisers (NHS number for `gb-eaw`). Mask identifier regions on images sent to vision (T-10).
2. T-07b sends up to `AiSettings.extraction_batch_size` pages (default 100) per Claude call, not one call per page, using the cached system prompt + JSON schema and the rate-limit scheduler in `core/ai/kit.py`. Requests and responses are page-keyed, and each page's `word_ids` are scoped to that page only. Use Bedrock batch where available in-region.
3. Every extracted item lists `word_ids`. `provenance.check()` rejects values not present in those words. Retry once, then mark `needs_human`.
4. Dates are normalised by code (`dateparser`) using the pack's date order (DMY under `gb-eaw`, MDY under the `zz-test` fixture); ambiguous dates are flagged "Please check", never guessed. The model's date is never final.
5. "As the clinician saw it" visibility is computed by code from `availability_evidence`, never decided by the model.
6. Every prompt has a version string. Changing a prompt or model requires `make eval` to pass.
7. Prompt templates are neutral: fact kinds, field schemas, record cues, vocabulary and date order are injected from `pack.prompt_context()`; output schemas are generated from the merged registries.

### Testing

- Pipeline tests run against synthetic bundles in `evals/gb-eaw/clin-neg-breach/general-practice/bundles/` with recorded OCR and Claude responses in `api/tests/fixtures/`.
- Expected facts with page and word references live in `evals/gb-eaw/clin-neg-breach/general-practice/expected/` (one folder per pack combination). Slice A's acceptance tests compare pipeline output to them.
- `make eval` runs real models nightly and fails if extraction precision drops below 0.95 or the share of pages sent to screen 10 rises by more than 5 points from the last baseline.
