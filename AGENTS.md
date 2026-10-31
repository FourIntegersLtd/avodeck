# Avodeck

Avodeck is a records-review engine for expert witnesses. Its launch configuration (packs) is England and Wales, clinical negligence breach of duty, general practice. An expert uploads an unsorted bundle of medical records (1,000–4,000 pages). Avodeck numbers every page, reads it, finds dates, results, consultations and prescriptions, and links every fact back to the exact words on the page. The expert reads every page, checks every suggestion, and writes a court-compliant report in their own words. Avodeck then supports the court stage: written questions, expert meetings, re-paginated court bundles, and the witness box. Everything specific to a country, case type or specialty comes from packs.

Read `docs/architecture.md` sections 1–3 before starting any work, then the sections your slice brief points to. Your slice brief is in `docs/slices/`.

## Non-negotiable rules

These are enforced by tests in `api/tests/guards/` and `web/src/foundation/guards/`. Never weaken them. If a task seems to need breaking one, stop and ask.

1. **The AI never writes opinion.** No prompt asks for a conclusion, an opinion or rewritten prose. AI output schemas contain no free-prose fields except short `reason` labels.
2. **Every fact has provenance.** Every extracted value cites OCR `word_ids` on a numbered page. The server rejects any value not found in those words (after normalising case, spacing and number format). Only `verified` facts can be cited in a report.
3. **Blind read is enforced by the server.** `GET /cases/{id}/pages/{n}/facts` returns 423 until `POST /cases/{id}/pages/{n}/read` is recorded. Never rely on the frontend to hide suggestions.
4. **The browser talks only to FastAPI.** No Supabase, Azure or AWS calls, keys or direct storage links in the frontend, and no product logic in the browser. Only two things work on the device itself: drawing the signature (sent to FastAPI as an image) and the offline witness-box search (`S25-Lookup`), which works with no connection.
5. **Data stays in the home region.** Each region is a separate cell (UK first: Supabase London, Bedrock London **in-region** model IDs only, never `global.*` or `eu.*` profiles, Azure UK South). The UK cell's API and worker run on Railway, Amsterdam, holding no data at rest (ADR 0021). Code reads every endpoint and model ID from the cell config; nothing region-specific is hard-coded.
6. **No patient text in logs.** Never log page text, OCR output, prompts, AI responses, names, identifier numbers (e.g. NHS numbers) or dates of birth. This includes Sentry, DigitalOcean logs and test output.
7. **Identifiers out before AI.** Presidio identifier stripping (`app/core/ai/pii.py`) runs before every Claude call.
8. **Served reports are locked.** A served `report_versions` row is immutable (409 on change). Corrections and changes of opinion go through the supplementary report flow (screen 21b).
9. **Access check on every case route.** Every `/cases/{case_id}/…` route calls `require_case_access(action)` (`docs/spec/permissions.md`): the named expert, a case member whose case role allows the action, or an organisation owner/admin for administrative actions only. Anyone else gets 404. Only the named expert verifies facts, writes opinions, signs and serves. Row-level security repeats the check: FastAPI connects as a restricted role with `SET LOCAL app.person_id` per request, never as the service role (ADR 0013).
10. **Step-up for risky actions.** Sign, send, export, delete and retention changes require MFA completed in the last 10 minutes (`aal2`).
11. **No country, case type or specialty is hard-coded.** Court rules, declaration wording, deadlines, legal tests, guidelines, vocabulary (claimant, solicitor, GP, trial bundle), date order, identifier formats, retention, workflow steps, question types, fact kinds, source types, timeline lanes, checks and report sections come from the case's packs (jurisdiction, case type, specialty and the jurisdiction × specialty overlay; at launch `gb-eaw`, `clin-neg-breach`, `general-practice`, `overlay/gb-eaw/general-practice`) via `app/core/packs.py`. So do party and instructing-party words, professions and registration bodies, timezones, court calendars and holidays, units, speech language and phrase lists, and AI prompt context and output schemas. Never write the forbidden words in `docs/architecture.md` 3.5 (e.g. CPR, PD35, NICE, statement of truth, solicitor, claimant, GP, EMIS, NHS number) in slice code, prompts or enums. Every interface string uses an i18n key. Core tests run under the fixture packs (`packs/_fixtures/`) to prove it.

## Stack

| Part | Choice |
|---|---|
| Frontend | React + TypeScript on Vercel; renders screens only; typed client generated from OpenAPI |
| API + worker | FastAPI (Python 3.12) on DigitalOcean App Platform, London; worker consumes a Postgres job queue |
| Database, auth | Supabase (London): Postgres + pgvector, Auth (called only by FastAPI) |
| Case files | AWS S3 (London, eu-west-2): private bucket, SSE-S3 + per-case envelope encryption (ADR 0032) |
| LLM | Claude Sonnet 5 / Opus 5.5 via AWS Bedrock, London in-region |
| OCR | Azure AI Document Intelligence (Layout, Read), UK South |
| Dictation | Azure Speech en-GB, UK South, relayed through FastAPI over WebSocket |
| Email | Resend, patient-free content only |
| Regions | One identical cell per region (`REGION=uk` first); jurisdiction packs for country rules |
| Errors | Sentry EU with scrubbing |

## How we work

### Test-first is mandatory

1. **Red:** write tests from the slice brief. Run them. Confirm they fail for the right reason. Commit as `test(<slice>): …`.
2. **Green:** write the smallest code that makes them pass. Do not edit tests in this step. Commit as `feat(<slice>): …`.
3. **Refactor:** tidy with tests green. Commit as `refactor(<slice>): …`.

If a test looks wrong, stop and explain why. Never change a test to make code pass. Use the `tdd-feature` skill for every feature.

### Slices and ownership

- Work happens in vertical slices (foundation, A–I, O). Each slice owns its screens, endpoints, tables, AI tasks and tests end to end. See `docs/architecture.md` section 8.4.
- A slice writes only its own tables and code (`api/app/features/<feature>/`, `web/src/capabilities/<feature>/`, its migration range). It reads other slices' data through their endpoints or the shared contracts in `api/app/core/contracts/`.
- **Foundation-owned (change only with approval):** `api/app/core/`, `web/src/foundation/ui/`, `web/src/foundation/platform-client/`, this file, area AGENTS.md files, `.claude/`, `.github/`.
- Contracts only grow. Endpoints may gain fields; never remove or rename one that another slice uses.

### Who does what

- Each slice has one owner agent in `.claude/agents/`; the paths each owner may change are in `.claude/OWNERS.md`.
- A prompt hook (`.claude/hooks/route_hint.py`, keywords in `.claude/routes.json`) suggests the owner for your prompt. It is a hint only; set `ROUTE_HINT_OFF=1` to turn it off.
- Hand slice work to that owner, or follow its brief.
- Owners change only their own paths. For anything in `api/app/core/`, contracts, the design system, the api client, packs or `.claude/`, ask `foundation-owner`.
- Run the `verify-slice` skill before calling work done.
- Owners: foundation-owner (0), pipeline-owner (A), reader-owner (B), setup-owner (C), understand-owner (D), write-owner (E), report-owner (F), after-serving-owner (G), close-owner (H), trial-owner (I), organisations-owner (O).

### Migration ranges

Foundation 0000–0099 · A 0100–0199 · B 0200–0299 · C 0300–0399 · D 0400–0499 · E 0500–0599 · F 0600–0699 · G 0700–0799 · H 0800–0899 · I 0900–0999 · O 1000–1099. Use the `add-migration` skill.

### Branches and commits

- One slice per git worktree and branch: `slice/<letter>-<short-name>`.
- Small pull requests, merged to `main` often, behind a feature flag if unfinished.
- Commit prefixes: `test`, `feat`, `refactor`, `fix`, `chore`, `docs`. CI checks every `feat` follows a failing `test`.

### Definition of done

- [ ] Tests written first, now green; coverage floor for the slice met
- [ ] OpenAPI regenerated and frontend client updated (`make api-client`)
- [ ] Screen matches its canvas board; copy uses `docs/spec/glossary.md`
- [ ] Accessibility checks pass (WCAG AA, buttons ≥ 52px, body text ≥ 17px)
- [ ] No rule in this file broken; reviewer session has checked the PR against it
- [ ] Behaviour questions answered (`.claude/skills/slice-brief/SKILL.md`); every rule in architecture section 10 for this slice has a test
- [ ] New tables in section 4, `docs/spec/data-dictionary.md` and `docs/spec/behaviour-index.md`; new statuses in `state-machines.md`; new actions in `permissions.md`
- [ ] `python scripts/check_spec.py` passes
- [ ] PR description carries the ticked `docs/spec/reviewer-checklist.md`
- [ ] Slice brief updated if scope changed

## Commands

Local secrets: **`api/.env` only** (from `api/.env.example`). `make -C api start` and `make -C api worker` load it. CI uses `scripts/local_api_env.sh` for ephemeral local Supabase.

```
make dev            # run API, worker, frontend and local Supabase
make test           # all tests
make test-slice S=pipeline # one slice's tests (api + web)
make api-client     # regenerate OpenAPI + typed frontend client
make eval           # nightly live AI evaluation (real models, synthetic bundles)
make lint           # ruff, mypy, eslint, tsc
make check-spec     # documents agree with each other (tables, endpoints, states, behaviour)
```

## Where things are

- `packs/`: the pack layers; see `docs/architecture.md` section 3
- `docs/architecture.md`: the build specification, most important first: rules (1), system shape (2), engine and packs (3), data model (4), API (5), AI (6), pipeline (7), build plan (8), security (9), **behaviour rules (10)**
- `docs/research/background.md`: research, vendor, cost, compliance and market notes. **Don't read it unless the task is about one of those decisions.**
- `docs/spec/`: glossary, design canvas link, **non-functional requirements** (numbers you are tested against), **state machines** (the only allowed status changes), **failure modes** (each row needs a test), **permissions** (who may do what), **scenarios** (end-to-end tests), **data dictionary** (every field in plain English), **behaviour index** (one line per table), reviewer checklist
- `docs/security/threat-model.md`: read before adding any new data flow, external service or sharing feature
- `docs/compliance/intended-purpose.md`: what Avodeck is and is not; never build a feature that breaks it without a decision record
- `docs/spikes/`: results of the pre-build experiments; they override assumptions elsewhere
- `docs/decisions/`: why we chose what we chose; read before proposing a change. `deferred.md` lists what we chose not to build yet and the trigger for each
- `docs/slices/`: owner briefs
- `docs/design/`: every screen as a picture (`images/`) and an HTML mock-up (`screens/`); the canvas link itself is private
- `evals/`: synthetic bundles and expected facts with page and word references

## Writing for experts

Our users are GPs aged roughly 50–60, not very computer-confident. Use the plain-language glossary in `docs/spec/glossary.md`. Large type, one main action per screen, no jargon (say "Pages you've read", not "coverage").
