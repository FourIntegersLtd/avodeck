# API and worker (FastAPI, Python 3.12, Poetry)

Read the root `AGENTS.md` first. This file covers `api/`: the API and the background worker, one Poetry project (`make -C api help` lists the commands).

## Layout

```
api/app/
  main.py                 # app factory, routers, middleware
  core/                   # foundation-owned
    auth/                 # cookie session, Supabase Auth calls, MFA, step-up, CSRF
    db.py                 # async Postgres (psycopg 3 pool), restricted roles, SET LOCAL per request/job (ADR 0013)
    storage.py            # Supabase Storage streaming upload/download
    jobs.py               # Postgres job queue, progress, server-sent events
    audit.py              # append-only, hash-chained audit log
    ai/                   # AI kit: pii.py, bedrock.py, ocr.py, provenance.py, calls_log.py
    speech.py             # WebSocket relay to Azure Speech
    contracts/            # shared Pydantic models: Fact, Question, ReportVersion, NeedsYouItem
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
- Risky routes (sign, send, export, delete, retention) depend on `require_step_up` (403 `step_up_required` without recent `aal2`).
- State-changing routes depend on `require_csrf`.
- Long work returns `202` + `{"job_id": ...}` and enqueues a job. Never do OCR, AI or document building inside a request.
- Errors use `ApiError(code, message, status)`. Codes: `not_found`, `blind_read`, `locked`, `step_up_required`, `conflict`, `invalid`.
- Files stream through `core/storage.py`. Responses carrying page images or documents set `Cache-Control: private, no-store`.
- Never return patient text in error messages.

## Auth

- The browser never sees Supabase tokens. `POST /v1/auth/sign-in` calls Supabase Auth server-side and sets an `httpOnly`, `Secure`, `SameSite=Strict` cookie.
- Verify Supabase access tokens against JWKS: check `aud`, `exp`, `sub`, `aal`. Never decode without verifying.
- 30-minute inactivity timeout and a maximum session length. Log sign-in success and failure to `audit_log`.

## Country-neutral code

- Get every legal or clinical rule from `pack = packs.for_case(case)`: `pack.deadline("written_questions", served_on)`, `pack.declarations()`, `pack.term("claimant")`, `pack.compliance_checklist()`, `pack.workflow_steps()`, `pack.fact_kinds()`, `pack.lanes()`, `pack.checks()`, `pack.date_order`, `pack.identifier_recognisers()`, `pack.party_roles()`, `pack.instructing_party_types()`, `pack.timezone`, `pack.speech()`, `pack.prompt_context()`.
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
result = await ai.extract(
    task_id="T-07b",
    model="sonnet",            # resolves to the London in-region model ID
    schema=PageExtraction,     # Pydantic model; sent as a tool schema
    payload=stripped_page,     # already through pii.strip()
    case_id=case_id,
)
validated = provenance.check(result, ocr_words)   # rejects values not in cited words
```

`ai_calls` logs task, model ID, region, prompt version and token counts. It never logs content.

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
| `build_report` | F | Assemble the report in the expert's Word template |
| `final_check` | F | 28 checks |
| `verify_opposing` | G | T-21b/c/d |
| `match_trial_bundle` | I | OCR (Read) + page matching |
| `build_trial_pack` | I | Large-print PDF |
| `export_case` | H | ZIP of the record of work |
| `delete_case` | H | Scheduled deletion + certificate |

### Rules

- **Idempotent per step and per page.** A job can be retried safely; every step checks whether its output exists before redoing work.
- **Bounded concurrency** for Azure OCR and Bedrock calls; respect rate limits with back-off.
- **Progress:** update `jobs.pct`, `jobs.step`, `jobs.eta_seconds`. No patient text in job rows or logs.
- **Pages keep their numbers forever.** `bundle_page_no` is set once. Appended records continue from the last number.
- **Duplicates are set aside, never deleted.**
- **Never auto-correct handwriting.** Low-confidence pages go to the "Pages we couldn't read" queue (screen 10).

### AI pipeline rules (from docs/architecture.md section 6)

1. `pii.strip()` before every Claude call, using the case's pack identifier recognisers (NHS number for `gb-eaw`). Mask identifier regions on images sent to vision (T-10).
2. One Claude call per page for extraction (T-07b), using the cached system prompt + JSON schema. Use Bedrock batch where available in-region.
3. Every extracted item lists `word_ids`. `provenance.check()` rejects values not present in those words. Retry once, then mark `needs_human`.
4. Dates are normalised by code (`dateparser`) using the pack's date order (DMY under `gb-eaw`, MDY under the `zz-test` fixture); ambiguous dates are flagged "Please check", never guessed. The model's date is never final.
5. "As the clinician saw it" visibility is computed by code from `availability_evidence`, never decided by the model.
6. Every prompt has a version string. Changing a prompt or model requires `make eval` to pass.
7. Prompt templates are neutral: fact kinds, field schemas, record cues, vocabulary and date order are injected from `pack.prompt_context()`; output schemas are generated from the merged registries.

### Testing

- Pipeline tests run against synthetic bundles in `evals/gb-eaw/clin-neg-breach/general-practice/bundles/` with recorded OCR and Claude responses in `api/tests/fixtures/`.
- Expected facts with page and word references live in `evals/gb-eaw/clin-neg-breach/general-practice/expected/` (one folder per pack combination). Slice A's acceptance tests compare pipeline output to them.
- `make eval` runs real models nightly and fails if extraction precision drops below 0.95 or the share of pages sent to screen 10 rises by more than 5 points from the last baseline.
