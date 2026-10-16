# Backend (FastAPI)

Read the root `CLAUDE.md` first. This file covers conventions for `backend/`.

## Layout

```
backend/app/
  main.py                 # app factory, routers, middleware
  core/                   # foundation-owned
    auth/                 # cookie session, Supabase Auth calls, MFA, step-up, CSRF
    db.py                 # async Postgres (asyncpg/SQLAlchemy core), service-role connection
    storage.py            # Supabase Storage streaming upload/download
    jobs.py               # Postgres job queue, progress, server-sent events
    audit.py              # append-only, hash-chained audit log
    ai/                   # AI kit: pii.py, bedrock.py, ocr.py, provenance.py, calls_log.py
    speech.py             # WebSocket relay to Azure Speech
    contracts/            # shared Pydantic models: Fact, Question, ReportVersion, NeedsYouItem
    packs.py              # pack loader: merges jurisdiction + case-type + specialty packs for a case
    config.py             # cell config: REGION, endpoints, model IDs, allowed packs
    flags.py              # feature flags
  slices/<slice>/
    router.py             # routes for this slice only
    schemas.py            # request/response Pydantic models
    service.py            # business logic
    repo.py               # SQL for this slice's tables only
backend/tests/
  core/                   # non-negotiable tests (foundation-owned)
  <slice>/                # tests per slice
  fixtures/               # recorded AI/OCR responses, synthetic pages
```

## Route conventions

- Prefix `/v1`. Case routes are `/v1/cases/{case_id}/…`.
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
- Only your slice's `repo.py` writes your slice's tables.

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
- The `core/` tests are the product's safety net. If one fails, stop.
