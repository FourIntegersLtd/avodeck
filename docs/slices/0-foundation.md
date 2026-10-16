# Foundation

Owner: the founder (with one Claude session) · branch `slice/0-foundation` · Phase: before all slices · Migrations 0000–0099 · Estimate 4–6 weeks part-time, in two stages

## Goal
A deployed, empty Avodeck that an expert can sign in to with MFA, create a case and upload a bundle, with every shared piece and safety rule in place so slices A–I and O can be built in parallel.

## Two stages

**Stage 1 (local, no cloud accounts):** project skeleton; local Supabase (Docker); `core/db.py` with restricted roles and RLS (ADR 0013); sign-in, MFA, CSRF, step-up against local Supabase Auth; people, organisations, memberships, case members, cases, documents, pages, jobs (Procrastinate), audit log, notifications and feature flags tables with state-machine triggers; `require_case_access`; ETag and Idempotency-Key; chunked uploads to local Storage; progress events; pack schemas and the four-layer loader with fixture packs; `provenance.check`; `pii.strip`; `core/crypto.py` with a **local key provider** behind the same interface; fake, recorded stand-ins for Bedrock, Azure OCR and Speech; frontend shell, design system, sign-in and Today; CI green including `scripts/check_spec.py`.

**Stage 2 (after spikes 1–4 and 8):** real Bedrock client pinned to London IDs; Azure OCR and Speech relay; AWS KMS key provider (ADR 0010); IAM users and policies (architecture 9); staging and production deploys (DigitalOcean App Platform London, Vercel); the acceptance tests that need real services run against staging.

**Stage 1 progress**
- [x] Step 1 (2026-10-16): project skeleton in the osceguide layout:
  - `api/` is one Poetry project on Python 3.12, holding the API and the worker (`app/worker/`). Features live in `app/features/<feature>/`; tests are grouped by kind, with the rule tests in `tests/guards/`.
  - `web/` uses `src/{app,foundation,capabilities}`.
  - Each package has an `AGENTS.md`, with a `CLAUDE.md` containing `@AGENTS.md`.
  - The root Makefile targets are `dev`, `test`, `test-core`, `test-slice S=<feature>`, `lint` and `check-spec`.
  - Local Supabase is set up in `supabase/config.toml`.
  - CI uses Poetry: `ci-api.yml` and `ci-web.yml`, plus `eval-nightly.yml`, which stays off until `EVAL_ENABLED` is set.
- [x] Step 2 (2026-10-16): database base (ADR 0013):
  - Migration `0000_foundation_base.sql` (down in `supabase/migrations/down/`): the `app` schema, owned by `app_migrator` and not exposed to the Data API; NOLOGIN roles `app_api`, `app_worker`, `app_migrator` (no superuser, no `BYPASSRLS`; runtime roles own nothing); default grants for future `app` tables; `app.current_person_id()`, `app.current_org_ids()`, `app.current_job_case_id()`, which return nothing when unset so RLS fails closed.
  - `supabase/seed.sql` gives the roles local-only passwords; cells set real ones out of band.
  - `core/db.py`: psycopg 3 pool; `Database.request(scope)` and `Database.job(case_id)` each run one transaction with transaction-local settings; refuses to start as a role that can bypass RLS. `core/config.py` reads `DATABASE_URL` and `DATABASE_URL_DIRECT`.
  - Local Supabase ports moved to 553xx (DB on 55322) to avoid clashes with other local projects.
  - Not yet: `app.can_access_case()` and the tables it reads arrive with the tables step.
- [x] Step 3 (2026-10-16): core tables and case access:
  - Migrations `0001`–`0004` (downs in `supabase/migrations/down/`): the core tables plus `idempotency_keys`, all with RLS for `app_api` (by person) and `app_worker` (by job case); state-machine triggers for `cases`, `jobs`, `memberships` and `pages` (SQLSTATE `AV409` → 409 `conflict`); `app.case_action_allowed()` (the permissions matrix, kept equal to `core/auth/permissions.py` by a test), `app.case_access()`, `app.can_access_case()`, `app.ensure_person()`; hash-chained `audit_log` (sequence + advisory lock; `core/audit.find_breaks()` recomputes it).
  - `core/auth/access.py` `require_case_access(action)` (404 without view rights, 403 without the action); `core/http.py` `router()` gives each request one transaction, Idempotency-Key replay (F27), `IfMatch`/`etag()` (428, 412 `stale`, F26); `core/errors.py`; `core/state_machines.py`; `core/config.cell_settings()` (`REGION`).
  - `GET /v1/me` (creates the person, a personal organisation and the owner membership) and `GET`/`PATCH /v1/cases/{case_id}`. Request and job transactions run in UTC.
  - Routers are added with `main.mount()`, not `include_router()`: FastAPI 0.143 includes routers lazily, which would hide their routes from the rule 9 route scan. Every slice router must be mounted this way.
  - Not yet: identity comes from a test seam until the auth step; step-up and CSRF arrive with auth; deadline timezones with packs.
- [x] Step 4 (2026-10-16): sign-in, MFA, CSRF and step-up:
  - Migration `0005_foundation_auth.sql` (down in `supabase/migrations/down/`): `sessions`, `mfa_recovery_codes`, `email_changes`, all private to their person under RLS; security-definer `app.session_resolve()` (cookie lookup, 30-minute idle and 12-hour expiry), `app.confirm_email_change()` and `app.audit_anonymous()` (failed sign-in and reset requests, address hashed).
  - `core/auth/supabase.py` is the only caller of local Supabase Auth (password, TOTP, recovery, admin) and checks every access token (ES256 via JWKS, audience, issuer, expiry, `aal`). `core/auth/session.py`: opaque `av_session` cookie (httpOnly, Secure, SameSite=Strict) and `av_csrf` double-submit token, both stored only as SHA-256. `core/http.py` `router()` now resolves the session, checks Origin, CSRF and MFA; `public_router()` is for the five signed-out routes the CSRF guard names. `require_step_up` and `require_case_access` for 🔒 actions need MFA within 10 minutes. `core/email.py` `OutboxMailer` is the local stand-in for Resend.
  - Endpoints: `/v1/auth/sign-in`, `/mfa/enroll`, `/mfa/verify` (also step-up), `/mfa/recovery-codes` 🔒, `/mfa/recover`, `/sign-out`, `/session`, `/password/forgot`, `/password/reset`; `/v1/me/sessions` (list, delete, `revoke-all` 🔒), `/v1/me/email-change` 🔒 and `/confirm`. The step 3 identity seam is gone; tests sign in through `client_for()` (seeded sessions) or `sign_in_with_mfa()` (real local Auth).
  - Choices: recovery needs the password too; passwords at least 12 characters; Auth tokens kept on the session row unencrypted until `core/crypto.py`; local Auth rate limits raised in `supabase/config.toml` for tests (cells rate-limit in FastAPI, not built yet); the reset email comes from Supabase Auth's local mailer with a template that links to the web app.
  - Not yet: per-client rate limiting and captcha on sign-in, leaked-password check, `make api-client` (no `app.export_openapi` yet), S01/S28 screens.
- [x] Step 5 (2026-10-16): packs:
  - `core/packs.py`: `load_library(*roots)` reads each pack set (`jurisdiction/`, `case-type/`, `specialty/`, `overlay/<j>/<sp>/`), resolves `extends:`, validates every file against `packs/schema/*.schema.json` (23 schemas; a file without one is refused), refuses duplicate YAML keys and duplicate registry keys, lints specialty words and codes out of jurisdiction and case-type files and country words and jurisdiction codes out of specialty files (overlays exempt), then merges every offered combination (3.2 rules; placeholders filled from the merged vocabulary plus `instructing_party`; every party role and instructing-party type needs a label). `MergedPack` gives settings, words, registries, declarations, legal tests, identifier recognisers, prompt context, record hints, speech and `deadline()`.
  - Deadlines: worked out in the court's timezone (aware datetimes only), `calendar_days` with `if_closed: next_working_day` or `business_days` over the pack's `working_week`, skipping the dates in the new `holidays.yaml`; `due_at` is the end of the due day in UTC and `shown_in(tz)` gives the expert's time. Outside the holiday snapshot's range it refuses (fail closed).
  - `create_app()` loads `default_library()` (`PACKS_DIR`, else `packs/` only; tests add `packs/_fixtures/`), so an invalid pack stops the app; `make -C api packs` (`python -m app.core.packs`) runs in CI. Endpoints: `GET /v1/jurisdictions`, `/v1/jurisdictions/{code}` (the cell's own only, else 404), `/v1/packs`, `/v1/cases/{case_id}/pack` (`case.view_metadata`). Guard `tests/guards/test_foundation_forbidden_words.py` scans slice code and prompt templates for `lint_words.yaml` `slice_code_forbidden` plus the country and specialty words.
  - Pack data: gb-eaw `deadlines.yaml` is now a `deadlines:` registry (written questions 28 calendar days, joint-statement signing and sending, trial and answers set by direction), `working_week` and `holidays.yaml` (England and Wales bank holidays 2026-2028); general-practice `facts.yaml` has fields per fact kind, `sources.yaml` has `{key, colour}`, `checks.yaml` uses `default_on`; overlay `records.yaml` key `record_systems`; fixture packs follow the same shapes (Sunday-Thursday week, 21 business days).
  - Choices: a cell offers a combination when the jurisdiction's `cell` matches, all three packs come from the same pack set and the jurisdiction registers the specialty's profession; the holiday list is a checked-in snapshot (no refresh job yet); the 28-day roll-forward rule is marked verify-before-launch.
  - Not yet: i18n-label and version-bump lint, pack lifecycle approval, screen copy under the fixture packs (frontend step), `make api-client`.

Acceptance tests that need a cloud service are written in stage 1 against the stand-ins and re-run against the real service in stage 2.

## Screens
- `S28-Account` Account and security (canvas board 33): devices, backup codes, email change, message preferences
- 01 Sign in (`S01-SignIn`)
- 03 Today shell (`S03-Today`): layout, case cards, and the "needs you" registry that slices add to
- App shell: case header with step pills, navigation, error and loading states
- Design-system components from `Main.dc.html`

## Endpoints (docs/architecture.md 5.2)
- `POST /v1/auth/sign-in`, `/auth/mfa/enroll`, `/auth/mfa/verify`, `/auth/sign-out`, `/auth/password/forgot`, `/auth/password/reset`, `GET /auth/session`
- `GET /v1/me`, `POST /v1/me/sessions/revoke-all` 🔒
- `POST /v1/cases`, `PATCH /v1/cases/{id}`, `GET /v1/today`
- `POST /v1/cases/{id}/uploads`, `PUT /v1/uploads/{upload_id}/chunks/{n}`, `POST /v1/uploads/{upload_id}/complete`
- `GET /v1/jobs/{job_id}`, `GET /v1/jobs/{job_id}/events` (SSE)
- `WS /v1/cases/{id}/dictation`, `POST /v1/cases/{id}/transcripts`
- `GET /health`

## Tables owned
`experts` (with `profession`, `specialties[]`, `registration_body`, `home_region`, `locale`, `timezone`, `currency`), `cases` (with `jurisdiction`, `party_role`, `instructing_party_type`, `court_track`), `documents`, `pages` (columns used by all, including `text_source`; slice A fills OCR and source fields), `jobs`, `audit_log`, `idempotency_keys`, `ai_calls`, `transcripts`, `feature_flags`. Contract tables created empty for slices to fill: `facts`, `questions`, `report_versions`.

## Shared code
- `core/auth/`: cookie session, Supabase Auth calls, JWKS verification, `require_case_access(action)`, `require_step_up`, `require_csrf`, inactivity timeout
- `core/storage.py`: chunked upload and streaming download via Supabase Storage
- `core/jobs.py`: Postgres queue (Procrastinate or pgmq), progress, SSE
- `core/audit.py`: hash-chained append-only log
- `core/ai/`: `pii.py` (Presidio + UK recognisers + case names), `bedrock.py` (London in-region only), `ocr.py` (Azure DI), `provenance.py`, `calls_log.py`, fixture recorder/replayer
- `core/speech.py`: WebSocket relay to Azure Speech en-GB
- `core/packs.py`: loader that validates and merges the four pack layers; `packs/jurisdiction/gb-eaw`, `packs/case-type/clin-neg-breach`, `packs/specialty/general-practice` and `packs/overlay/gb-eaw/general-practice` filled in; JSON Schemas in `packs/schema/` are written first (they don't exist yet)
- Registries for fact kinds, source types, question types, timeline lanes, checks and report sections, extended by packs; case header steps from `workflow.yaml`
- `organisations` and `memberships` (every expert a one-person organisation at launch); `case_type`, `specialty`, `expert_role`, `pack_versions` on cases
- `core/crypto.py`: per-case envelope encryption with AWS KMS London (ADR 0010); `case_keys` table; key cache ≤ 5 min
- Database triggers enforcing `docs/spec/state-machines.md` for cases, facts, questions, report versions and jobs
- `packs/schema/*.json`: JSON Schema for every pack file; CI fails on an invalid pack; loader refuses to start with one
- `core/config.py`: cell config (`REGION=uk`), endpoints and model IDs, allowed packs, AI allow-list
- i18n set-up: `web/src/foundation/i18n/en-GB.json` and the translation helper; every foundation string uses a key
- `core/contracts/`: `Fact`, `FactStatus`, `Question`, `ReportVersion`, `NeedsYouItem`; `facts.citable_facts(case_id)` (signature and core test here, implemented by B); `pipeline.py` (entry point A implements and B/G call); `events.py` (`report_served`, emitted by F, consumed by G); `case_tables.py` registry of every case table (used by deletion in slice H; a core test fails if a case table is missing from it)
- Frontend: generated API client with CSRF header, design-system components, `DictationBox`, case shell, Today
- CI, nightly eval workflow, staging + production deploys (DigitalOcean App Platform London: api, worker, scheduled jobs; Vercel: frontend)

## Acceptance tests (write first; all must fail before work starts)
**Non-negotiables (`api/tests/guards/`, `web/src/foundation/guards/`):**
- [x] Every `/v1/cases/{id}/…` route returns 404 for a person with no access to that case (route table scanned automatically)
- [x] State-changing routes return 403 without a CSRF token
- [x] 🔒 routes return 403 `step_up_required` without `aal2` in the last 10 minutes
- [ ] `GET /pages/{n}/facts` returns 423 until `POST /pages/{n}/read` (tested against the contract with a stub route)
- [ ] `provenance.check` rejects a value not present in its cited words; accepts after normalising case, spaces, number formats
- [ ] Updating a `report_versions` row with `locked_at` set fails (409 via API, error at DB level)
- [ ] `bedrock.py` refuses any model ID that is not a London in-region ID
- [ ] A request containing a made-up NHS number and name produces no log line containing either
- [ ] `pii.strip` removes names from the case name list, NHS numbers (mod-11 valid), postcodes and dates of birth; keeps event dates
- [ ] Frontend build contains no Supabase/Azure/AWS hosts or SDKs; CSP allows only the API origin
- [ ] Frontend never writes tokens to `localStorage` or `sessionStorage`
- [x] No slice code or prompt template contains a word from the forbidden list in architecture 3.5 (scan of `api/app/features`, `web/src/capabilities`, prompt templates)
- [ ] **Fixture packs** (`packs/_fixtures/`) (API side done in step 5; screen copy waits for the frontend step): core API flows and screen copy run under both the launch packs and the fixture packs; party words, clinician titles, deadlines (business days, timezone), date order, identifiers, workflow steps, lanes and declarations all change accordingly
- [x] Pack loader merges four layers (jurisdiction, case type, specialty, overlay) with the rules in architecture 3.2, supports `extends:`, resolves placeholders, and refuses a jurisdiction file containing a specialty word or a specialty file containing a country word
- [x] Deadlines respect the pack's calendar rule and public holidays (test: a deadline landing on an England and Wales bank holiday)
- [x] All timestamps stored in UTC; deadlines shown in the expert's timezone
- [ ] Every frontend string comes from an i18n key (no hard-coded user-facing text in components)
- [ ] The AI kit refuses model IDs and endpoints outside the current cell's allow-list
- [x] `GET /v1/jurisdictions` returns only `gb-eaw` in the UK cell; `GET /v1/jurisdictions/gb-eaw` returns vocabulary, declarations, checklist and deadline rules
- [x] `pack.deadline("written_questions", served_on)` returns served_on + 28 days for `gb-eaw`
- [x] A status change not in `docs/spec/state-machines.md` is refused by the service (409) and by the database trigger
- [ ] `crypto.encrypt`/`decrypt` round-trip per case; after key destruction, decrypt fails; KMS unreachable → processing pauses (fail closed)
- [x] An invalid pack file fails CI schema validation and stops the app starting
- [ ] Failure modes F5, F6, F10, F11 and F24 (`docs/spec/failure-modes.md`) each have a passing test

**Features:**
- [x] Sign-in sets an httpOnly, Secure, SameSite=Strict cookie and never returns a token in the body
- [x] First sign-in requires MFA enrolment; sign-in returns `mfa_required` until the code is verified
- [x] Session expires after 30 minutes of inactivity; sign-in success and failure are written to `audit_log`
- [x] Password reset responds the same whether or not the email exists, and revokes all sessions
- [ ] A 300 MB PDF uploads in 8 MB chunks, resumes after a dropped chunk, and is stored with a matching SHA-256
- [ ] Job progress streams over SSE and contains no patient text
- [ ] Dictation: audio streamed over WebSocket returns text; no audio is stored; no Azure key reaches the browser
- [x] Audit log hash chain verifies; tampering with a row is detected
- [ ] Today shows case cards and items registered through `NeedsYouItem`
- [ ] Sign-in and Today screens pass axe checks and match their canvas boards

## Out of scope
Bundle processing (A), reader (B), profile and questions (C), everything after.

## Definition of done
Root checklist, plus: deployed to staging in London; a test expert can sign in with MFA, create a case and upload a bundle; all core tests green.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `people`, `sessions`, `mfa_recovery_codes`, `email_changes`, `organisations` (personal only), `memberships`, `case_members` (table and access check; screens in C), `notifications`, `notification_preferences`, `feature_flags`, `case_keys`, `deletion_ledger` (schema; H writes).

**Extra endpoints:** `GET /v1/me/sessions`, `DELETE /v1/me/sessions/{session_id}`, `POST /v1/auth/mfa/recovery-codes` 🔒, `POST /v1/auth/mfa/recover`, `POST /v1/me/email-change` 🔒, `POST /v1/me/email-change/confirm`, `GET/PUT /v1/me/notification-preferences`, `GET /v1/notifications`, `POST /v1/notifications/{notification_id}/read`, `GET /v1/feature-flags`, `GET /v1/packs`, `GET /v1/jurisdictions`, `GET /v1/jurisdictions/{code}`, `GET /v1/cases/{case_id}/pack`, (The Stripe webhook moved to slice O, Phase 2.)

**Shared code:** `core/auth/require_case_access(action)` is the only access check (it replaces the earlier owner-only check); it reads the matrix in `docs/spec/permissions.md` from one table in code. ETag/If-Match middleware (412 `stale`); `Idempotency-Key` middleware (24 h); per-case job lock; scheduled-job runner keyed by date; notification service (in-app + email, no patient data, bounce banner).

- [x] Every cell of the permissions matrix has a passing test under the fixture packs
- [x] First `GET /me` creates a `people` row and a personal organisation with the person as owner
- [x] A person who is named expert on one case and assistant on another gets the right rights on each (S1)
- [x] A stale `If-Match` returns 412 and changes nothing (F26)
- [x] A repeated `Idempotency-Key` returns the first response and creates one row (F27)
- [ ] A missed daily run catches up each missed date exactly once (F28)
- [x] Recovery codes: 10 issued, each works once, regenerate invalidates the old set; admins cannot reset another person's MFA
- [x] Email change needs confirmation from both addresses and step-up
- [x] Audit rows carry person, organisation, case, action and target
- [ ] Only one case-changing job runs per case at a time; interactive jobs run before batch
