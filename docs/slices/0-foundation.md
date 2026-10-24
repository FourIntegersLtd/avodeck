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
  - `create_app()` loads `default_library()` (`PACKS_DIR`, else `packs/` only; tests add `packs/_fixtures/`), so an invalid pack stops the app; a production cell sets `PACKS_APPROVED_ONLY=1` and then refuses any pack without `status: approved`, `approved_by` and `approved_on` (10.11); `make -C api packs` (`python -m app.core.packs`) runs in CI. Endpoints: `GET /v1/jurisdictions`, `/v1/jurisdictions/{code}` (the cell's own only, else 404), `/v1/packs`, `/v1/cases/{case_id}/pack` (`case.view_metadata`). Guard `tests/guards/test_foundation_forbidden_words.py` scans slice code and prompt templates for `lint_words.yaml` `slice_code_forbidden` plus the country and specialty words.
  - Pack data: gb-eaw `deadlines.yaml` is now a `deadlines:` registry (written questions 28 calendar days, joint-statement signing and sending, trial and answers set by direction), `working_week` and `holidays.yaml` (England and Wales bank holidays 2026-2028); general-practice `facts.yaml` has fields per fact kind, `sources.yaml` has `{key, colour}`, `checks.yaml` uses `default_on`; overlay `records.yaml` key `record_systems`; fixture packs follow the same shapes (Sunday-Thursday week, 21 business days).
  - Choices: a cell offers a combination when the jurisdiction's `cell` matches, all three packs come from the same pack set and the jurisdiction registers the specialty's profession; the holiday list is a checked-in snapshot (no refresh job yet); the 28-day roll-forward rule is marked verify-before-launch.
  - Not yet: i18n-label and version-bump lint, pack lifecycle approval, screen copy under the fixture packs (frontend step), `make api-client`.
- [x] Step 6 (2026-10-16): AI kit and encryption (all cloud services are recorded stand-ins):
  - `core/ai/`: `pii.strip` (Presidio with a blank spaCy tokeniser; case names, the pack's identifier recognisers, postcodes, e-mail addresses and known dates of birth in any written form become `[KIND_n]` placeholders; event dates stay), `provenance.check`/`check_items` (case, spacing and number-format normalised), `cell.py` (model IDs and endpoints only from the cell allow-list; `global.*`, `eu.*` and non-https hosts refused), `RecordedBedrock`, `RecordedOcr` and `core/speech.py` `RecordedSpeech` (recordings keyed by request hash; documents and audio stored only as hashes), `kit.py` `AiKit.run` (allow-list, cost guard, strip, schema check, back-off, one `ai_calls` row per answer).
  - Failure modes: F5 back-off then `ProcessingPaused` (503 `processing_paused` with `reason`), F10 canaries in `tests/fixtures/ai/canary.json` for launch and fixture packs plus `logs.find_identifiers`, F11 cost guard (`app.ai_budget`: 3 x expected tokens a page x max(pages, minimum); alert logged), F24 fail closed with no fallback.
  - `core/logs.py`: every log record is scrubbed when made (URL paths, queries, e-mail addresses, pack identifiers, long numbers; exceptions keep type and frames only); requests logged by route template; HTTP client INFO lines off; Sentry options with no PII, bodies, locals or messages (`SENTRY_DSN` empty locally).
  - `core/crypto.py`: `CaseCrypto` per-case envelope encryption (AES-GCM) with `LocalKeyProvider` behind the `KeyProvider` interface, keys cached at most 5 minutes; key destruction makes decrypt fail; provider unreachable pauses processing. `SecretBox` seals the Supabase Auth tokens on the session row (`av1:` prefix enforced by a check constraint).
  - Migration `0006_foundation_ai_and_keys.sql`: `ai_calls` and `case_keys` (append only, RLS), `app.ai_budget()`, `app.destroy_case_key()` (worker, own job only, audited), session token constraint (old plaintext tokens cleared).
  - Choices: `case_keys.key_ref` instead of a KMS ARN column (provider-neutral); sealed tokens bound to purpose and person; date-of-birth matching uses the pack language's month names; Presidio's e-mail recogniser only (no trained NER: names come from the case name list); gb-eaw and zz-test identifier files gained labelled date-of-birth recognisers (pack versions bumped); one test fix commit (the record-and-replay test counted a second client's request on the first).
  - Not yet: real Bedrock, Azure and KMS clients (stage 2), image masking for vision (T-10), Bedrock batch, F6 (jobs step).
- [x] Step 7 (2026-10-17): files, jobs and the dictation relay:
  - Migration `0007_foundation_files_jobs.sql`: Procrastinate 3.10 schema in its own `procrastinate` schema (only `app_worker` may use it); `jobs.step`; `uploads` (state machine trigger), `job_steps`, `scheduled_runs`, `transcripts`, with RLS; `app.enqueue_job()` (checks case access, writes the `jobs` row and queues it with the lock `case:<id>` for case-changing jobs), `app.claim_scheduled_run()` and `app.last_scheduled_run()` (worker only).
  - `core/jobs.py`: `registry.kind()`/`schedule()`/`enqueue()`, `JobContext.progress()`/`step()`, `JobWorker` (interactive priority before batch, one case-changing job per case, retries then `failed`/`job_error`, `ProcessingPaused` → `paused`, `requeue_stalled()` (F6), `catch_up()` by date (F28)). `app/worker/main.py` `build_worker()` runs it as `app_worker` on `DATABASE_URL_DIRECT`.
  - `core/storage.py` (local Supabase Storage, service role for Storage only) and `core/routes/uploads.py`: `POST /v1/cases/{id}/uploads`, `PUT /v1/uploads/{id}/chunks/{n}`, `GET /v1/uploads/{id}`, `POST /v1/uploads/{id}/complete`, `GET /v1/cases/{id}/documents/{document_id}/file`; chunks encrypted under the case key; whole-file SHA-256 checked on complete; `document.uploaded` audited.
  - `core/routes/jobs.py`: `GET /v1/jobs/{id}` and `/events` (SSE: status, step, pct, eta_seconds, error_code only; changes only; ends when finished or after 5 minutes).
  - `core/routes/dictation.py`: `WS /v1/cases/{id}/dictation` (refuses before accepting: 4401, 4403, 4404, 4422; pack locale and phrases; `speech_unavailable` + 1011 when the service cannot answer) and `POST /v1/cases/{id}/transcripts` (encrypted, `transcript.saved` audited). `require_socket_case_access()` in `core/auth/access.py`; guard `test_foundation_websocket_routes.py`.
  - Choices: dictation and transcripts use `comment.write` (no new permission action); 500 MB file limit; one Storage object per chunk; bucket `STORAGE_BUCKET` (default `case-files`); unfinished uploads expire after 7 days; WebSockets require Origin.
  - Not yet: virus scan and `process_bundle` on complete (slice A), live Azure Speech (stage 2), the frontend.
- [x] Step 8 (2026-10-17): shared contracts and the OpenAPI export:
  - Migration `0008_foundation_contracts.sql`: `facts`, `questions`, `report_versions` with state-machine triggers (`AV409`), version bumps, no deletes and RLS (view `case.view_content`; write `fact.verify`, `question.confirm`, `report.build`; worker its job's case only). A locked report version refuses every change except served and withdrawn (SQLSTATE `AVLCK` → 409 `locked`). `app.expire_uploads(run_date)` (worker only, audited).
  - `core/contracts/`: `Fact`, `citable_facts()`, `Question`, `ReportVersion`, `ensure_unlocked()`, `NeedsYouItem`, `CaseCard`, `needs_you` provider registry, `require_page_read()` (423 `blind_read` per person), `PageReadLog`, `EventBus` (`ReportServed`), pipeline job names, `CASE_TABLES`. Scanned for forbidden words.
  - `GET /v1/today` (`core/routes/today.py`): open case cards with your role, closed count, your organisations, and registered items for the cases shown, ranked; a failing provider is skipped (type logged only).
  - `core/housekeeping.py`: daily `expire_uploads` schedule (chunks deleted from Storage; rows only when Storage is not configured). `app.export_openapi` and `make api-client` (web step skipped until the frontend exists); `/openapi.json` committed and tested against the app.
  - Choices: the read gate uses the `page.read` action and reads are per person; citable = verified or edited on a page the named expert has read; `questions.instruction_version_id` has no foreign key until slice C adds `instruction_versions`.
  - Not yet (frontend, step 9): CSP and no cloud hosts in the build, no tokens in browser storage, screen copy under the fixture packs, i18n keys, Sign-in/Today axe and canvas checks. Not yet (stage 2): staging deploy in London and real Bedrock, Azure, KMS. Not yet built: `GET /v1/notifications`, `POST /v1/notifications/{id}/read`, `GET/PUT /v1/me/notification-preferences`, `GET /v1/feature-flags` (next API step).
- [x] Step 9 (2026-10-17): the frontend shell (`web/`, Vite + React + TypeScript, pnpm):
  - `foundation/platform-client`: client generated from `openapi.json` (`make api-client`), `unwrap()`/`ApiError`, CSRF header from the `av_csrf` cookie, `onSignedOut`, `idempotencyKey`, `ifMatch`, `websocketUrl`. `make api-client` also writes `pack-fixtures.json` (launch and fixture packs as the API returns them; `case_pack()` builder in `core/routes/packs.py`), tested up to date.
  - `foundation/ui`: tokens (body 18px, buttons 52px, canvas colours), `Button`/`LinkButton`, `TextField`/`CodeField`, `Checkbox`, `Notice`, `StatusBadge`, `CaseHeader` (pack steps, tick when done, `aria-current="step"`), `Dialog`, `DictationBox` (WebSocket to the API; type-instead messages), `AppShell`, `Logo`. `foundation/lib`: screen registry, step-up dialog (rule 10), session, errors, pack party words. `foundation/i18n`: `en-GB.json`, plurals, `registerMessages`, `Intl` dates.
  - Screens: S01 sign in (password, code, first set-up with QR and key, backup codes shown once, backup-code recovery then re-set-up, forgotten password), reset-password and email-confirm link pages, S03 Today (greeting in the person's timezone, ranked needs-you items with registered words and screens, case cards with pack party word, step and due date, organisation filter, counts), S28 security part (email change, password link, code, new backup codes, devices, sign out one or everywhere else with step-up, sign out). A minimal case frame at `/cases/{id}` (name and pack steps).
  - Guards (`web/src/foundation/guards/`): no cloud host or SDK in source or build; build-time CSP (`web/csp.ts`) allows only `'self'` and the API origin; no browser storage or cookie writes (static and during sign-in); no hard-coded JSX text; i18n keys exist; forbidden words in `capabilities`, `app`, `foundation/{ui,i18n,lib}`; Today and the case header under launch and fixture packs. Playwright: sign in to Today, Today axe with colour contrast, 52px/17px.
  - Choices: same-origin `/v1` through the Vite proxy in development, `VITE_API_ORIGIN` in production (a cross-origin API needs CORS and the CSRF cookie domain: stage 2); password change on S28 sends the reset link (no change-password endpoint); step names come from `steps.<key>` keys or the pack key; organisations, notifications and closing the account stay with slice O.
  - Not yet: side-by-side canvas review, notifications and feature flags (API not built), stage 2 hosting (Vercel headers).

- [x] Step 10 (2026-10-17): notifications, flags, CI and the canvas review:
  - Migration `0022_foundation_hold_closed_requester.sql` (requests from C and H): a case on hold keeps its jobs (`queued`, `case_on_hold`; `registry.kind(..., runs_on_hold=True)` for kinds that deal with the hold) and messages (`notifications.held_at`; `notify(..., while_on_hold=True)` for the hold itself) back until `core/case_hold.release_case_hold()` on resume; `cases.closed_at` (settled or withdrawn); `jobs.requested_by` (set by the database); `app.case_member_names()` behind `core/contracts/members.py`. `core/pdf_document.py` `render_record()` (reportlab) for records handed out as a PDF. ADR 0015: audit rows stay unchanged on deletion.
  - Migration `0009_foundation_notifications.sql`: `notification_preferences.enabled`, `notifications.in_app`, `people.email_bounced_at`. `core/notifications.py` `notify()` (in-app row and/or a patient-free email per the person's choices for that organisation), daily `notifications_daily` digest schedule, `mark_bounced()`. `core/flags.py` `evaluate()`, `is_enabled()`, `require_flag()` (per cell, organisation or person; off when unknown).
  - Endpoints: `GET /v1/notifications`, `POST /v1/notifications/{notification_id}/read`, `GET`/`PUT /v1/me/notification-preferences`, `GET /v1/feature-flags`. Web: messages page, header bell with the unread count, bounce banner, "How we tell you things" on S28.
  - `POST /v1/cases` (`core/routes/cases.py`): a draft case named to the caller, who must be an owner, admin or expert of the organisation (403 otherwise); jurisdiction, case type and specialty must be a combination the cell offers, and expert role, party role, instructing party type and court track must be keys of that merged pack (422 `invalid`); expert role defaults to the case type's first; pack versions are stamped and `case.created` audited. Migration `0010_foundation_case_create.sql` adds the `cases_create` RLS policy repeating that check. An assistant naming another expert waits for slice C/O.
  - Every operation documents the shared `ApiError` body as its `default` response; a 405 carries `Allow` with every method the path offers; sign-in, recover, forgot and reset refuse control characters (422 `invalid`); the server's own access log is off (`RequestLog` logs by route template). All found by Schemathesis.
  - CI: `scripts/local_api_env.sh` gives the API its local settings; `ci-api.yml` starts Supabase without the unused services, starts the API and runs Schemathesis (`api/schemathesis.toml`), and has Node and pnpm for the red-then-green check; `ci-web.yml` takes the pnpm version from `web/package.json`. Run locally: lint, types, packs, guards, tests, Schemathesis, red-then-green, every web step and Playwright.
  - Canvas: S01 (brand panel, avocado logo, promise points, note), S03 (needs-you card, organisation filter in the cases header, card grid, progress ring) and S28 security part (one "Signing in" list with code and backup codes, devices with "Sign out everywhere else" in the header, message choices) match their boards. Playwright axe with colour contrast on S01, S03 and S28.
  - Slice A prerequisites (2026-10-17): `core/pdf.py` (`split_pages`, deterministic single-page PDFs; `embedded_words` from the text layer, IDs `p1w<n>`, inches), `core/ai/embed.py` (`RecordedEmbedder`, 384 dimensions) and `core/scan.py` (`RecordedScanner`, EICAR), `core/ai/answers.py` (`AnswerBook` keyed by `task_id` + `page_subject`/`reference_subject`, nth call gets the nth answer), `OcrWord.struck`, `ModelRequest.task_id`/`subject`, `kit.run(subject=)`, `contracts/pipeline.py` (`register_process_pages`, `process_pages`), `contracts/events.py` (`DocumentUploaded` published in the upload-complete transaction, `subscribe_in_transaction`), `uploads.append`, migration 0017 (`scan_document`, `reprocess_case` job kinds), pack keys `quarantine_purge_days` and `vocabulary.instructing_party_request`. The generator writes layout sidecars and `evals/generator/recordings.py` writes OCR and T-07b/T-07c recordings for the whole synthetic bundle (planted retry, bad-every-time and crossed-out answers) and an 18-page offline subset in `api/tests/fixtures/ai/pipeline/`; the expected files were regenerated (page numbers of planted items moved; font measurements differ between machines).
  - Slice E prerequisites (2026-10-18): case-type `opinion.yaml` (`point_sides` exactly supports_claim/supports_clinician/neutral, `opinion_views`, `loaded_language`, `range_note_prompt`; `{clinician}`-style placeholders filled from the vocabulary), `MergedPack.point_sides/opinion_views/loaded_language/range_note_prompt`, `prompt_context` for T-14a, T-14b, T-14c and T-15a, `CasePack.point_sides/opinion_views/range_note_prompt`, migration 0023 (`suggest_points`, `compare_witness_statement` job kinds need `opinion.write`).
  - Choices: no support phone number on S01 (none decided); the logo word uses the UI font (Outfit is not bundled); the S01 promise stays neutral where the board names the clinician (no pack is known before sign-in); the greeting uses the full name; Cases, profile, help and New case links wait for slice C; organisations, records custodian and closing the account stay with slice O. Schemathesis runs signed out, so 30 operations are fuzzed only up to the sign-in check.

### Changes for slice C (2026-10-17, founder-approved plan, phase F)

- Actions (permissions.md, `core/auth/permissions.py`, `app.case_action_allowed`, migration 0019): `case.hold` (named expert, assistant; replaces `case.edit_setup` for `on_hold`), `conflict.declare` (named expert), `case.change_jurisdiction` (named expert, step-up). The database checks each sensitive `cases` column against its own action.
- `POST /v1/cases`: `named_expert_id` (only an assistant may name another expert of the organisation), `subject_status`, `period_start`/`period_end`. Subject statuses come from the case-type pack.
- `instruction_versions` is a foundation contract table (0017) that slice C fills; it does not create it. `core/contracts/questions.py`: `questions_locked(conn, case_id)` and `require_questions_locked(action)` (409 `questions_not_confirmed`). A lock is never undone; a new version reopens the questions.
- Packs (`MergedPack`): `coverage_roles()` (specialty, labels checked at merge), `outside_expertise_question_types()`, `conflicts()` (optional jurisdiction `conflicts.yaml`), `normalise_identifier()` (optional per jurisdiction; `None` when the pack has none or the value is not valid), `prompt_context(task)` per task (T-06, T-04, T-02; T-02 uses the pack's report sections plus `other`, not the architecture 6.3 enum). Fixture packs carry each key.
- `core/crypto.py`: `KeyProvider.mac()`, `subject_key_hash(normalised)` (HMAC-SHA256 under a cell key, purpose `subject_key`; `ProcessingPaused` when the key service is unreachable).
- Wiring: `app.features.setup` router mounted at `/v1`, imported by the worker; web `setupRoutes` and `setupCaseRoutes` (case child routes render inside the case frame). The shell shows Cases, Your profile and Help only once a slice registers those screens.
- Fixtures: `make setup-fixtures` writes `api/tests/fixtures/setup/{letter-of-instruction.pdf,template.docx}` (deterministic); recordings go in `api/tests/fixtures/ai/{bedrock,ocr}/setup-*.json`; setup-owner owns those paths (OWNERS.md). Generator deps are in the Poetry `evals` group; python-docx and pillow are main deps.
- Note: slice C's brief says the fixture case period is Feb–Aug 2024; `evals/.../expected/case.json` has 2024-02-01 to 2024-09-30. The expected file is the source; the brief needs correcting by setup-owner.
- Note: `docs/slices/C-setup.md` still lists `instruction_versions` under tables owned; it is now foundation-created and C-filled.
- `core/contracts/case_tables.py`: slice C's case tables (migration 0310) are registered for case deletion: `case_parties`, `case_instructing_parties`, `case_links`, `case_instructions`, `case_subject_keys` (test 47145cf).
- `core/contracts/case_tables.py`: slice C's later case tables (migrations 0320-0340) are registered: `conflict_answers`, `conflict_declarations`, `case_withdrawals`, `assumptions`, `instruction_extractions`, `prefill_suggestions`, `prefill_runs` (test 26119a0).
- `web/src/foundation/i18n/en-GB.json`: slice C added its `setup.*` keys (screens S02, S04, S05, S06, the Today items and the notes) in its feat commit 02138a5.
- `core/contracts/case_tables.py`: `conflict_answer_history` (slice C migration 0370, review finding 7) is registered for case deletion (test commit before this one).
- `web/src/foundation/i18n/en-GB.json`: slice C added `setup.questions.warning.read_failed`, `setup.questions.warning.letter_without_file`, `setup.questions.read_again`, `setup.questions.letter_not_read` and `setup.conflicts.warning.declare_again` (review findings 4, 5 and 7).
- `core/contracts/questions.py`: `questions_locked()` skips oral instructions (kind `oral`): they carry no questions, so they never reopen a confirmed set nor hide its lock from the next letter (slice C review finding 4; test commit before this one).
- Request (done): tests under `web/src/app/` now start with an empty screen registry (`resetScreens` in `foundation/lib/screens.ts`, called from `web/src/test/setup.ts`), so slice C registers the `profile` screen key without breaking `ShellLinks.test.tsx`.

### Changes for slice D (2026-10-18, founder-approved)

- Packs: lanes carry `kinds` and optional `when` (first lane whose `when` matches, else the first plain lane listing the kind; every kind must be a fact kind); vocabulary `lanes` labels and `as_seen_title` (placeholders filled at merge); check rows carry their rule's parameters (`checks.schema.json`: rule enum, `flag` as a word, `{field, value}` or `{field, any_of}`, `acknowledges_field`, `or_kinds`, `scope`, `label_spans`); overlay `guidelines.yaml` `versions` and `reviewed_at` (labels, dates, links only); `own_record` source types; `arrives` fact kinds. Accessors `lanes()`, `guidelines()`, `guideline_sources()`, `own_record_sources()`, `arriving_kinds()`. Fixture packs gain a `when` lane and two checks; they ship no overlay (tests that need one add it to a temporary copy, so fixture cases stay three layers).
- Actions (migration 0023): `link.suggest` (case readers), `link.decide`, `finding.decide`, `availability.override`, `consultation.note` (named expert). Job kinds `suggest_links` → `link.suggest`, `rerun_checks` → `finding.decide`. `app.set_fact_availability(fact, available_at, basis)` (worker in the case's own job, or `availability.override`; no new version).
- `core/features.py`: feature start-up hooks `on_startup(app)` and `on_worker_start(worker)`. Test helper `tests/support/facts_seed.py` `insert_fact()`.

### Stage 2: live AI clients (2026-10-18, founder-approved)

- `core/ai/azure_ocr.py` `AzureOcr` (Azure AI Document Intelligence v4 REST `2024-11-30`, `prebuilt-layout` / `prebuilt-read`): submit, poll `Operation-Location` (only on the allow-list and the endpoint's own host), words to `p<page>w<n>` with polygons and handwriting from `styles`; `struck` stays False (the service does not report it). 429/5xx/timeouts: growing waits honouring `Retry-After`, then `ServiceBusy`; other 4xx or a failed analysis: `OcrFailed` (code only).
- `core/ai/bedrock_live.py` `LiveBedrock` (boto3 `bedrock-runtime` Converse in `AWS_REGION` at `BEDROCK_ENDPOINT`): the task schema is the only tool and is forced, so the tool input is the structured output `AiKit` validates; the SDK's retries are off (the kit backs off on `ServiceBusy`); missing or withdrawn model is `ModelUnavailable`; botocore/boto3/urllib3 loggers held at WARNING (rule 6).
- `core/ai/clients.py` `live_or_recorded()`: per service, live when `ai_settings()` and its keys are present, else recorded when `AI_RECORDINGS_DIR` is set, else none. Keys only from the cell config, never an SDK default credential chain. The setup, understand, write and report `from_env` and the pipeline `wiring.from_env` call it.
- **Going live:** set the cell values `ai_settings()` already needs (`REGION`, `AWS_REGION`, `BEDROCK_SONNET_MODEL_ID`, `BEDROCK_OPUS_MODEL_ID` as in-region IDs, `BEDROCK_ENDPOINT`, `AZURE_DI_ENDPOINT`, `AZURE_SPEECH_ENDPOINT`, `AI_ENDPOINT_ALLOW_LIST` naming all three hosts), then `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (optional `AWS_SESSION_TOKEN`) switch the model to live and `AZURE_DI_KEY` switches OCR to live. Unset them to fall back to `AI_RECORDINGS_DIR`.
- Not yet: live Azure Speech relay, clamd virus scanner, bge-small embedder, KMS key provider, Bedrock batch; a live smoke run against the real services once the accounts exist.

**Stage 1 status (2026-10-17):** stage 1 is complete. The API (auth with MFA, CSRF and step-up, restricted roles with RLS, case access, ETag and Idempotency-Key, chunked encrypted uploads, jobs with progress events, the audit hash chain, packs, the AI kit with recorded stand-ins, local key provider, contracts, Today, notifications and flags) and the web shell (S01, S03, S28 security part, design system, typed client, i18n) run against local Supabase with 764 API tests (95% coverage), 80 web tests and Playwright with axe, all green; CI runs the same commands. What is left is stage 2 and needs cloud accounts: real Bedrock (London in-region IDs), Azure OCR and Speech, the AWS KMS key provider, IAM, Resend, Sentry, the staging and production deploys in London (DigitalOcean App Platform, Vercel headers, CORS and the CSRF cookie domain for a cross-origin API), the acceptance tests re-run against those services, and the definition-of-done item "deployed to staging in London; a test expert can sign in with MFA, create a case and upload a bundle" (the API part, `POST /v1/cases`, is built and tested; the new-case screen is slice C's S04).

Acceptance tests that need a cloud service are written in stage 1 against the stand-ins and re-run against the real service in stage 2.

### Changes for slice O (2026-10-22, founder-approved plan, Phase 0)

- [x] `support_staff` (operators add and remove staff by hand, `docs/ops/support-staff.md`; no route writes it) and `support_access_grants` (shared core table, kept after case deletion as the record of consent; O builds the routes), migration 0030
- [x] Case role `support` in `app.case_access()` and `require_case_access`: current staff and a live grant from the named expert (24 hours, checked on read); only `case.view_metadata`, `case.view_content`, `case.view_audit`; every request audited `support_access.viewed`; fresh MFA (`aal2`) on every call; `require_support_staff` (10-minute step-up) for support routes that change something, `require_support_lookup` (MFA at sign-in for the session, ADR 0020) for read-only lookups
- [x] Closed accounts (S15): read-only on served cases where they are the named expert; everything else 404, in the API and RLS
- [x] `require_org_access(action)` for the organisation-action table (non-members 404, members without the right 403, 🔒 step-up)
- [x] Contracts `org_policy()`, `request_case_deletion()`, `case_export_done()`; mails `org.invite`, `case.reassigned`, `support_access.granted`

### Follow-ups for slice O (2026-10-23, founder-approved)

- [x] S3: a change of named expert puts every checked fact back to a suggestion (`cases_named_expert_reset`, migration 0031; `facts_guard` exception and `fact_verifications.action = 'reset'` in 0212, in slice B's range because it must follow 0208 and 0210). Chosen over "only checks by the current named expert count" because every slice reads `facts.status` directly; resetting keeps one truth. An edited fact's suggestion is restored from the verification log; its box is drawn again (`app.word_box`).
- [x] Replacement case contract `core/contracts/replacement.py` `create_replacement_case()` over `app.create_replacement_case()` and `case_replacements` (migration 0033): `case.reassign_named_expert` 🔒 on a served case; a new draft named to another expert member, linked `replaces` (slice C's `case_links`), consent recorded; the caller does not join (`cases_creator_joins` redefined). Bundle copy stays with slice A.
- [x] Custody transfers: `custody_transfers` (kept after deletion), case role `custodian` (read actions only) in `app.case_access()`, `app.case_role_allowed()` and `require_case_access`, migration 0032; permissions.md custodian column. Slice O builds `POST /v1/support/custody-transfers` behind `support_custody_transfer`.
- [x] Support job actions (support-account plan phase 3, migration 0035): core router `core/routes/support.py`; `POST /v1/support/jobs/{job_id}/resume` (flag `support_job_resume`; paused job back on the queue, the worker moves it to running) and `POST /v1/support/jobs/{job_id}/requeue` (flag `support_job_requeue`; failed -> queued, attempts 0); `{reason_code}` from a fixed list; `job_support_actions` keeps what the job was; audited `support.job_resumed` / `support.job_requeued` on the case trail; mails `support.job_resumed`, `support.job_requeued`. Slice O adds the console buttons.

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
- `POST /v1/cases/{id}/uploads`, `PUT /v1/uploads/{upload_id}/chunks/{n}`, `GET /v1/uploads/{upload_id}`, `POST /v1/uploads/{upload_id}/complete`, `GET /v1/cases/{id}/documents/{document_id}/file`
- `GET /v1/jobs/{job_id}`, `GET /v1/jobs/{job_id}/events` (SSE)
- `GET /v1/notifications`, `POST /v1/notifications/{notification_id}/read`, `GET`/`PUT /v1/me/notification-preferences`, `GET /v1/feature-flags`
- `WS /v1/cases/{id}/dictation`, `POST /v1/cases/{id}/transcripts`
- `GET /health`

## Tables owned
`experts` (with `profession`, `specialties[]`, `registration_body`, `home_region`, `locale`, `timezone`, `currency`), `cases` (with `jurisdiction`, `party_role`, `instructing_party_type`, `court_track`), `documents`, `pages` (columns used by all, including `text_source`; slice A fills OCR and source fields), `jobs`, `job_steps`, `scheduled_runs`, `uploads`, `audit_log`, `idempotency_keys`, `ai_calls`, `transcripts`, `feature_flags`. Contract tables created empty for slices to fill: `facts`, `questions`, `report_versions`.

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
- `core/contracts/`: `Fact`, `FactStatus`, `Question`, `ReportVersion`, `NeedsYouItem`; `facts.citable_facts(case_id)` (signature and core test here, implemented by B); `pipeline.py` (entry point A implements and B/G call); `events.py` (`report_served`, emitted by F, consumed by G); `case_tables.py` registry of every case table (used by deletion in slice H; a core test fails if a case table is missing from it), with `PURGE_ORDER` (children first, = `app.case_purge_order()`) and `AWAITING_SLICE_MIGRATION` (tables registered before their slice creates them)
- Close support for slice H (migration 0017): `app.purge_case(case_id) -> jsonb` (worker, in the case's own job; refused while held), `app.cases_due_for_deletion(date)`, `app.replay_deletion_ledger()` (F29), `cases.settled_at`, actions `case.settle`/`case.archive`/`case.reopen`/`case.view_audit`/`case.view_certificate`; `core/email.py` `deletion_certificate_mail()`; `core/storage.py` `case_prefix()`, `list_recursive()`, batched `delete()`; `core/features.py` mounts `app.features.<name>.router` and the worker imports `app.features.<name>.jobs`; `ctx.mailer` in jobs
- Deleted cases (migration 0018): a deleted case answers only `case.view_certificate` (named expert, organisation owners/admins); everything else is 404. `app.case_access(case_id, action)` returns a `deleted` column; the one-argument form still hides deleted cases. Web capability routes mount by convention (`web/src/app/capabilityRoutes.ts`: every `<name>Routes` array exported from `src/capabilities/<feature>/index.tsx`). Tests and `scripts/check_red_green.py` use the checkout's own local Supabase port (`api/tests/support/local_supabase.py`).
- Report inputs for slice F (migration 0026): `core/contracts/profile.py` (`report_template`, `declarations`, `signature`, `conflict_declaration`) through security-definer functions `app.case_report_template`, `app.case_declarations`, `app.case_signature`, `app.case_conflict_declaration`, `app.case_conflict_answers` keyed by case: the named expert's rows only, to the case's own job or `report.build` (`report.sign` for the signature); slice C's table policies unchanged. `core/contracts/ai_calls.py` `ai_call_summary` (metadata only, under `ai_calls` RLS).
- Frontend: generated API client with CSRF header, design-system components, `DictationBox`, case shell, Today
- CI, nightly eval workflow, staging + production deploys (DigitalOcean App Platform London: api, worker, scheduled jobs; Vercel: frontend)

## Acceptance tests (write first; all must fail before work starts)
**Non-negotiables (`api/tests/guards/`, `web/src/foundation/guards/`):**
- [x] Every `/v1/cases/{id}/…` route returns 404 for a person with no access to that case (route table scanned automatically)
- [x] State-changing routes return 403 without a CSRF token
- [x] 🔒 routes return 403 `step_up_required` without `aal2` in the last 10 minutes
- [x] `GET /pages/{n}/facts` returns 423 until `POST /pages/{n}/read` (tested against the contract with a stub route)
- [x] `provenance.check` rejects a value not present in its cited words; accepts after normalising case, spaces, number formats
- [x] Updating a `report_versions` row with `locked_at` set fails (409 via API, error at DB level)
- [x] `bedrock.py` refuses any model ID that is not a London in-region ID
- [x] A request containing a made-up NHS number and name produces no log line containing either
- [x] `pii.strip` removes names from the case name list, NHS numbers (mod-11 valid), postcodes and dates of birth; keeps event dates
- [x] Frontend build contains no Supabase/Azure/AWS hosts or SDKs; CSP allows only the API origin
- [x] Frontend never writes tokens to `localStorage` or `sessionStorage`
- [x] No slice code or prompt template contains a word from the forbidden list in architecture 3.5 (scan of `api/app/features`, `web/src/capabilities`, prompt templates)
- [x] **Fixture packs** (`packs/_fixtures/`) (API side step 5, screen copy step 9): core API flows and screen copy run under both the launch packs and the fixture packs; party words, clinician titles, deadlines (business days, timezone), date order, identifiers, workflow steps, lanes and declarations all change accordingly
- [x] Pack loader merges four layers (jurisdiction, case type, specialty, overlay) with the rules in architecture 3.2, supports `extends:`, resolves placeholders, and refuses a jurisdiction file containing a specialty word or a specialty file containing a country word
- [x] Deadlines respect the pack's calendar rule and public holidays (test: a deadline landing on an England and Wales bank holiday)
- [x] All timestamps stored in UTC; deadlines shown in the expert's timezone
- [x] Every frontend string comes from an i18n key (no hard-coded user-facing text in components)
- [x] The AI kit refuses model IDs and endpoints outside the current cell's allow-list
- [x] `GET /v1/jurisdictions` returns only `gb-eaw` in the UK cell; `GET /v1/jurisdictions/gb-eaw` returns vocabulary, declarations, checklist and deadline rules
- [x] `pack.deadline("written_questions", served_on)` returns served_on + 28 days for `gb-eaw`
- [x] A status change not in `docs/spec/state-machines.md` is refused by the service (409) and by the database trigger
- [x] `crypto.encrypt`/`decrypt` round-trip per case; after key destruction, decrypt fails; KMS unreachable → processing pauses (fail closed)
- [x] An invalid pack file fails CI schema validation and stops the app starting
- [x] Failure modes F5, F6, F10, F11 and F24 (`docs/spec/failure-modes.md`) each have a passing test (F5, F10, F11, F24 in step 6; F6 in step 7)

**Features:**
- [x] Sign-in sets an httpOnly, Secure, SameSite=Strict cookie and never returns a token in the body
- [x] First sign-in requires MFA enrolment; sign-in returns `mfa_required` until the code is verified
- [x] Session expires after 30 minutes of inactivity; sign-in success and failure are written to `audit_log`
- [x] Password reset responds the same whether or not the email exists, and revokes all sessions
- [x] A 300 MB PDF uploads in 8 MB chunks, resumes after a dropped chunk, and is stored with a matching SHA-256
- [x] Job progress streams over SSE and contains no patient text
- [x] Dictation: audio streamed over WebSocket returns text; no audio is stored; no Azure key reaches the browser
- [x] Audit log hash chain verifies; tampering with a row is detected
- [x] Today shows case cards and items registered through `NeedsYouItem`
- [x] Sign-in and Today screens pass axe checks and match their canvas boards (axe in Vitest and in Playwright with colour contrast for S01, S03 and S28; side-by-side review against `docs/design/images/` done in step 10)

## Out of scope
Bundle processing (A), reader (B), profile and questions (C), everything after.

## Definition of done
Root checklist, plus: deployed to staging in London; a test expert can sign in with MFA, create a case and upload a bundle; all core tests green. (Stage 2: the staging deploy needs cloud accounts; core tests are green in stage 1.)

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `people`, `sessions`, `mfa_recovery_codes`, `email_changes`, `organisations` (personal only), `memberships`, `case_members` (table and access check; screens in C), `notifications`, `notification_preferences`, `feature_flags`, `case_keys`, `deletion_ledger` (schema; H writes), `mfa_attempts`, `job_kind_actions`.

**Extra endpoints:** `GET /v1/me/sessions`, `DELETE /v1/me/sessions/{session_id}`, `POST /v1/auth/mfa/recovery-codes` 🔒, `POST /v1/auth/mfa/recover`, `POST /v1/me/email-change` 🔒, `POST /v1/me/email-change/confirm`, `GET/PUT /v1/me/notification-preferences`, `GET /v1/notifications`, `POST /v1/notifications/{notification_id}/read`, `GET /v1/feature-flags`, `GET /v1/packs`, `GET /v1/jurisdictions`, `GET /v1/jurisdictions/{code}`, `GET /v1/cases/{case_id}/pack`, (The Stripe webhook moved to slice O, Phase 2.)

**Shared code:** `core/auth/require_case_access(action)` is the only access check (it replaces the earlier owner-only check); it reads the matrix in `docs/spec/permissions.md` from one table in code. ETag/If-Match middleware (412 `stale`); `Idempotency-Key` middleware (24 h); per-case job lock; scheduled-job runner keyed by date; notification service (in-app + email, no patient data, bounce banner).

- [x] Every cell of the permissions matrix has a passing test under the fixture packs
- [x] First `GET /me` creates a `people` row and a personal organisation with the person as owner
- [x] A person who is named expert on one case and assistant on another gets the right rights on each (S1)
- [x] A stale `If-Match` returns 412 and changes nothing (F26)
- [x] A repeated `Idempotency-Key` returns the first response and creates one row (F27)
- [x] A missed daily run catches up each missed date exactly once (F28)
- [x] Recovery codes: 10 issued, each works once, regenerate invalidates the old set; admins cannot reset another person's MFA
- [x] Email change needs confirmation from both addresses and step-up
- [x] Audit rows carry person, organisation, case, action and target
- [x] Only one case-changing job runs per case at a time; interactive jobs run before batch
