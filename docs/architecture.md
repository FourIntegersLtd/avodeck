# Avodeck architecture

**Who this is for:** anyone (or any Claude Code session) building Avodeck. It holds only what you need to build: the rules, the system shape, the engine and packs, the data model, the API, the AI tasks, the pipeline and the build plan, in that order.

**Not in here:** research evidence, vendor comparisons, costs, compliance analysis and market notes. Those live in `docs/research/background.md`. You do not need them to build; read them only when making a vendor, cost, legal or market decision.

**Also read:** the root `AGENTS.md` (rules and how we work), your slice brief in `docs/slices/`, and `docs/decisions/` before changing anything structural.

---

## 1. What we're building and the rules that never bend

Avodeck is a records-review engine for expert witnesses. A bundle of records goes in; every page is numbered and read; facts are pulled out, each tied to the exact words on its page; the expert checks them, arranges them on a timeline and against their questions, and writes their own conclusions with page references; a compliant report comes out with a full audit trail. It then supports the court stage (written questions, expert meetings, re-paginated court bundles, the witness box).

**Launch configuration:** England and Wales · clinical negligence, breach of duty · general practice. Everything else (other specialties, causation and prognosis, personal injury, other countries) is added later as packs, not rewrites (section 3).

**The non-negotiables** (full wording and tests in the root `AGENTS.md`):
1. The AI never writes opinion.
2. Every fact cites OCR word IDs on a numbered page; the server rejects anything else; only verified facts can be cited.
3. Blind read is enforced by the server (facts return 423 until the page is read).
4. The browser talks only to FastAPI.
5. Processing stays in the home region (UK cell at launch).
6. No patient text in logs.
7. Identifiers are stripped before every AI call.
8. Served reports are locked; changes go through a supplementary report.
9. Access is checked on every case route (`require_case_access(action)`: named expert, case member or organisation admin, by action).
10. Step-up (recent MFA) for sign, send, export, delete and retention.
11. No country, case type or specialty is hard-coded; everything that varies comes from packs; every UI string uses an i18n key.

---

## 2. System shape

### 2.1 Stack (UK cell)

| Part | Service | Region | Patient data? |
|---|---|---|---|
| Frontend | React + TypeScript on Vercel; renders screens only | Global CDN | No |
| API | FastAPI on DigitalOcean App Platform (no servers to manage) | London | Yes |
| Worker | Same codebase, App Platform worker component; Postgres job queue | London | Yes |
| Scheduled jobs | App Platform scheduled component (deadlines, deletion) | London | Yes |
| Database, auth, files | Supabase: Postgres + pgvector, Auth (called only by FastAPI), private Storage | London | Yes |
| LLM | Claude Sonnet 5 / Opus 5.5 via AWS Bedrock, in-region model IDs only | London | Yes, identifiers stripped |
| OCR | Azure AI Document Intelligence (Layout, Read) | UK South | Yes |
| Dictation | Azure Speech (language and phrase list from the case's packs; en-GB at launch), relayed through FastAPI over WebSocket | UK South | Yes |
| Email | Resend | Outside UK | No (patient-free content only) |
| Errors / billing / analytics | Sentry EU (scrubbed) · Stripe · PostHog EU cookieless | — | No |

### 2.2 How a request flows

- **Browser → FastAPI only.** Login, uploads (8 MB chunks), page images, downloads, dictation (WebSocket) and progress (server-sent events) all go through FastAPI. No Supabase, Azure or AWS keys or URLs in the frontend. Exceptions: the signature pad and the offline witness-box search.
- **FastAPI → Postgres** as restricted roles (`app_api`, `app_worker`), never the service role. Each request or job sets who is acting (`SET LOCAL app.person_id` / `app.job_case_id`), so row-level security really repeats the access check (ADR 0013). Tables live in a schema not exposed to Supabase's Data API. The service-role key is used only for Supabase Auth admin calls and Storage.
- **Long work** (OCR, extraction, report building, matching, export, deletion) is queued; the API returns `202` + `job_id`; the worker processes it page by page, idempotently.
- **AI calls** go only through the AI kit (`api/app/core/ai/`), which strips identifiers, enforces the cell's model allow-list, validates structured output, checks provenance and logs metadata (never content).

### 2.3 Regional cells

Each region is a complete, identical deployment with its own data. Nothing patient-related is shared between regions.

| Cell | App + worker | Database, auth, files | Claude | OCR + speech | Status |
|---|---|---|---|---|---|
| **uk** | DigitalOcean App Platform, London | Supabase, London | Bedrock, London in-region | Azure UK South | Launch |
| **au** | DigitalOcean, Sydney | Supabase, Sydney | Bedrock, Sydney (check in-region models) | Azure Australia East | Later |
| **ca** | DigitalOcean, Toronto | Supabase, Canada | Bedrock, Canada (check models) | Azure Canada Central | Later |
| **nz** | Served from the **au** cell if NZ customers and law accept it; AWS and Azure have NZ regions but Supabase and DigitalOcean do not | — | — | — | Decide later |

- **One codebase, configured per cell:** `REGION=uk|au|ca`. The cell's config supplies Supabase project, storage bucket, Bedrock model IDs and region, Azure endpoints, Resend domain, Stripe account settings, and the list of packs allowed in that cell.
- **Region-locked:** the AI kit refuses any model ID or endpoint not in the cell's allow-list (generalises the UK-only rule).
- **Addresses:** experts use `uk.avodeck.com`, `au.avodeck.com`; the API is `{region}.api.avodeck.com`.
- **Tiny global layer:** marketing site plus a directory of `email → region` for sign-in routing. No patient data, no case data.
- **No cross-region moves of case data.** An expert who works in two countries has an account in each cell.

---

## 3. The engine and packs

### 3.1 Engine building blocks (never specific to a country, case type or specialty)

| Block | What it is |
|---|---|
| Bundle, document, page | Uploaded files split into pages with permanent numbers; appended records continue the numbering |
| OCR words | Every word on every page with its position and confidence |
| Fact | A typed item pulled from a page (kind from the fact-kind registry), with word IDs, bounding box, dates and status (suggested / verified / rejected / edited) |
| Verification and page-read logs | Append-only, hash-chained records of what the expert checked and read |
| Timeline | Facts arranged by date in lanes (lanes come from the specialty pack) |
| Availability | When each item became visible to the clinician, computed by code, for "As the clinician saw it" |
| Questions | The instructing party's questions, typed by the case-type pack |
| Findings | Results of "Things to look at" rules (rules defined as data in packs) |
| Workspace and citations | The expert's own text, with citation objects pointing at verified facts |
| Report | Assembled from the case-type pack's sections into the expert's own Word template; locked and hashed when served |
| Deadlines and meetings | Generated from pack rules; court directions override |

### 3.2 Pack layers

Every case carries three codes (jurisdiction, case type, specialty). The loader merges **four** layers, in this order; later layers add to or override earlier ones:

1. **Jurisdiction** (the country or legal system)
2. **Case type** (the kind of work)
3. **Specialty** (the expert's field, written without any country in it)
4. **Overlay**: jurisdiction × specialty (the things that depend on both, such as the record systems used, the guidance bodies, the local title for the clinician, drug monitoring tables and dictation phrase lists). Optional; most combinations need only a few files.

| Layer | Launch pack | Holds | Later examples |
|---|---|---|---|
| **Jurisdiction** | `gb-eaw` (England and Wales) | Court rules, declarations and statement-of-truth wording, deadlines, meeting types, legal tests, date order, identifier formats, retention, vocabulary (claimant, solicitor, trial bundle) | `gb-sct`, `au-nsw`, `ca-on` |
| **Case type** | `clin-neg-breach` | Workflow steps and which screens appear, question types, report sections and compliance checklist, final checks | `clin-neg-causation`, `pi-condition-prognosis`, NHS incident review |
| **Specialty** | `general-practice` | Profession, fact kinds and their fields, source types, timeline lanes, "Things to look at" checks (rules as data), specialty final checks, neutral vocabulary ("general practitioner") | `emergency-medicine`, `orthopaedics`, `obstetrics`, `dental`, `physiotherapy` |
| **Overlay** | `gb-eaw/general-practice` | Record systems and their cues (EMIS, SystmOne), guideline sources and licences (NICE), local clinician title ("GP"), drug-monitoring table, dictation phrase list, local source-type names | `au-nsw/general-practice` (Best Practice, MedicalDirector; RACGP; "GP"), `ca-on/general-practice` ("family physician") |

```
packs/
  jurisdiction/gb-eaw/      pack.yaml, vocabulary.yaml, declarations.md, deadlines.yaml, holidays.yaml, meetings.yaml,
                            legal_tests.md, report.yaml (court compliance items), final_checks.yaml, identifiers.py
  case-type/clin-neg-breach/ workflow.yaml (steps + screens), questions.yaml, report_sections.yaml (section and appendix keys with their headings), final_checks.yaml, opinion.yaml (point-side, opinion-view, loaded-word and range-note wording)
  specialty/general-practice/ pack.yaml (profession), facts.yaml, sources.yaml, lanes.yaml, checks.yaml, final_checks.yaml, vocabulary.yaml
  overlay/gb-eaw/general-practice/ records.yaml, guidelines.yaml, vocabulary.yaml, speech_phrases.yaml, drug_monitoring.yaml
  _fixtures/                 deliberately strange test packs (see 3.6); never allowed in a real cell
```

- **One loader:** `api/app/core/packs.py` validates every pack against a JSON Schema (`packs/schema/`) and refuses incomplete ones.
- **Inheritance:** a pack may declare `extends: <code>` in its layer (e.g. `ime-au-nsw-workers-comp` extends `ime`; `gb-sct` could extend a shared `gb-common`). Child files override parent files key by key.
- **Merge rules:** dictionaries merge by key, later layer wins; lists that are registries (fact kinds, lanes, checks, sections, final checks) are unioned by `key`, and a later layer can disable an entry with `enabled: false`; text files (declarations, legal tests) are replaced whole. A key defined twice in the same layer is an error.
- **Placeholders, not words:** pack text that needs another layer's word uses placeholders, e.g. `{clinician}`, `{clinicians}`, `{instructing_party}`, `{subject}`, filled at merge time. A jurisdiction file must never contain a specialty word, and a specialty file never a country's word (pack lint in CI).
- **Versioned:** each pack has a version; a served report stores the versions it was built with.
- **Tested per combination:** synthetic bundles and the nightly evaluation run per jurisdiction × case type × specialty in `evals/`.
- **Guardrail against sprawl:** a new pack must reuse at least 70% of existing screens; if not, it's a product decision, not a pack.

### 3.3 Registries and workflow

- **Registries the packs extend:** fact kinds (with field schemas), record source types, question types, party roles, instructing-party types, expert roles, document kinds, deadline kinds, timeline lanes, "Things to look at" checks, report sections, final checks, meeting types, court tracks, written-question classifications. Code never uses fixed lists for these.
- **Rule types in code, rule instances in packs:** the checks engine implements a small set of generic rule types once (no_action_within, not_acknowledged_within, monitoring_overdue, not_reached_clinician, actions_not_done, field_missing, not_followed_up). Packs only choose and parameterise them, so a new specialty adds checks without new code. A genuinely new rule type is an engine change with its own tests.
- **Lanes, sources and kinds carry their meaning:** a lane lists its fact `kinds` and may add `when: {field: value}`; a fact goes to the first lane whose kinds include its kind and whose `when` matches, else to the first lane listing its kind with no `when`. Lane labels and the "As the clinician saw it" title are vocabulary (`lanes`, `as_seen_title`). A source type may be `own_record` (the clinician's own record) and a fact kind may `arrive` from outside (results, letters: never available on their own event date). Each check row carries its rule's parameters (`checks.schema.json`). The overlay's `guidelines.yaml` lists guideline `versions` in force (labels, dates and links only, decision 0005).
- **Expertise boundaries:** the specialty pack lists question types usually outside its expertise (general practice: causation); the case-type pack never names a specialty.
- **Workflow from the case type:** the case header's steps and the screens inside each step come from `workflow.yaml`. Breach shows "As the clinician saw it" and "Points against"; a later condition-and-prognosis pack adds "Examination" and "Injuries and prognosis" and hides "As the clinician saw it".

### 3.4 Neutral naming

- **i18n from day one:** every interface string uses a translation key; en-GB is the base with small overrides for en-AU, en-NZ and en-CA. The plain-language glossary becomes one file per locale. Dates, numbers and currency use the expert's locale.
- **Locale-aware dates:** the date normaliser takes the pack's default date order. It flags any ambiguous date (e.g. 03/04/24) as "Please check" instead of guessing.
- **Dictation language:** the pack sets the speech language (en-GB, en-AU, en-NZ, en-CA) and the phrase list (local drug names, abbreviations).
- **Identifier stripping:** recognisers come from the pack (NHS number now; Medicare, NHI and provincial health numbers later).
- **Registration:** an expert profile records the professional registration body (GMC now; AHPRA, CPSO, MCNZ later) instead of a GMC-only field.
- **Billing:** currency and tax per billing profile (person or organisation); Stripe Tax handles VAT/GST/HST.
- **Retention:** default retention period from the pack, not a hard-coded six years. Policies are `delete_now`, `keep_default` (years from the pack) and `return_then_delete`.
- **Time:** all timestamps stored in UTC. Each jurisdiction pack sets its `timezone` (or a list, for countries with several); each expert has a `timezone`. Deadlines are calculated in the court's timezone and shown in the expert's.
- **Court calendars:** deadline rules state `calendar_days` or `business_days`, and the jurisdiction pack names its public-holiday source (England and Wales: GOV.UK bank holidays), its `working_week`, and keeps a dated snapshot in `holidays.yaml`; a deadline outside the snapshot's range is refused. Never assume weekends or holidays in code.
- **Parties and instructing parties:** the case type declares party roles (e.g. `instructing_side`, `other_side`, `joint`) and instructing-party types (solicitor, insurer, coroner, scheme, regulator); the jurisdiction pack labels them (claimant, defendant, plaintiff). Code never says claimant or solicitor.
- **Professions:** the specialty pack declares the profession (doctor, nurse, physiotherapist, psychologist, dentist); the jurisdiction pack maps each profession to its registration bodies (GMC, NMC, HCPC; AHPRA). Experts can hold more than one specialty.
- **Court tracks and question rules:** optional jurisdiction registries (e.g. England and Wales tracks; how written questions are classified). A jurisdiction without them simply hides those fields.
- **Units and number formats:** values are stored exactly as printed; any unit conversion or normal ranges for display come from the overlay pack, never from code.
- **Language:** OCR, extraction prompts and dictation take their language from the packs, so a French-language jurisdiction (Quebec) is a pack plus i18n files, not a code change.
- **Compliance per country:** a privacy impact assessment, terms, processor agreement and sub-processor list for each cell.

**Renamed concepts:**

| UK-specific (old) | Neutral (new) |
|---|---|
| `p35_questions`, `/p35` | `written_questions`, `/written-questions` (rule_ref holds "CPR 35.6") |
| `joint_statements`, `/joint-statements` | `expert_meetings`, `/expert-meetings` (type: joint statement, joint report, conclave) |
| `trial_bundles`, `/trial-bundles` | `court_bundles`, `/court-bundles` |
| PD35 checklist | compliance checklist (PD35 for England and Wales) |
| `/statement-of-truth` | `/jurisdictions/{code}` (declarations come from the pack) |
| `gmc_number` | `registration_body` + `registration_number` |
| `hourly_rate_gbp` | `hourly_rate` + `currency` |
| `guideline_versions` (NICE only) | `guideline_versions` with `jurisdiction` and `source` |
| `solicitor_requests`, `/solicitor-requests`, `/solicitor-notes`, `tell-solicitor` | `instructing_party_requests`, `/instructing-party-requests`, `/instructing-party-notes`, `tell-instructing-party` |
| `cases.solicitor_name`, `cases.side` (claimant/defendant/joint), `cases.track` | `cases.instructing_party_name` + `instructing_party_type`, `cases.party_role` (pack key), `cases.court_track` (optional pack key) |
| `gp_actions_requested`, `sent_to_gp` | `actions_requested_of_clinician`, `sent_to_clinician` |
| `keep_6_years` | `keep_default` (years from the pack) |
| `experts.specialty` | `experts.specialties[]` + `profession` |

Screen names stay plain English for each locale (e.g. "Questions from the other side", "Trial page numbers"); only the words that differ come from the pack.

Also neutral: "clinician" (not GP) in code and logic; "subject" in code (the pack displays patient, claimant or deceased); "instructing party" (the pack displays solicitor).

### 3.5 How code uses packs

- **One entry point:** `pack = packs.for_case(case)` (in `api/app/core/packs.py`) returns the merged pack from all four layers (jurisdiction, case type, specialty, and the jurisdiction × specialty overlay), cached and version-stamped.
- **Examples:** `pack.deadline("written_questions", served_on)` (uses the pack's calendar and timezone), `pack.declarations()`, `pack.term("instructing_side")`, `pack.party_roles()`, `pack.instructing_party_types()`, `pack.registration_bodies(profession)`, `pack.timezone`, `pack.retention_years`, `pack.quarantine_purge_days`, `pack.instructing_party_request(**slots)`, `pack.speech()`, `pack.prompt_context()`, `pack.workflow_steps()`, `pack.question_types()`, `pack.fact_kinds()`, `pack.lanes()`, `pack.checks()`, `pack.own_record_sources()`, `pack.arriving_kinds()`, `pack.guidelines()`, `pack.guideline_sources()`, `pack.report_sections()`, `pack.compliance_checklist()` (with `page_limit` per court track), `pack.page_limits()`, `pack.document_kinds()`, `pack.outside_expertise_parts()`, `pack.subject_statuses()`, `pack.date_order`, `pack.identifier_recognisers()`, `pack.record_hints()`.
- **Never hard-code** in slice code, prompts, schemas, enums or tests outside fixtures: court or rule names (CPR, PD35, Part 35), guidance bodies (NICE), "statement of truth", party or instructing-party words (claimant, defendant, solicitor), clinician titles (GP), record systems (EMIS, SystmOne), identifier types (NHS number), currencies, date orders, timezones, holiday rules, retention periods or fixed lists of anything in a registry.
- **Recorded on the report:** `cases.pack_versions` and each served report store the pack versions used, so later wording changes never alter old reports.
- **Tested per combination:** `evals/<jurisdiction>/<case-type>/<specialty>/` holds synthetic bundles and expected facts.

### 3.6 Proving the engine is pack-driven

- **Fixture packs:** `packs/_fixtures/` holds a deliberately strange combination: `zz-test` (month-first dates, a different identifier format, 21-day deadlines in business days, a non-London timezone, "petitioner" and "counsel" instead of claimant and solicitor), `zz-case` (different workflow steps and question types) and `zz-specialty` (a non-doctor profession, different fact kinds and lanes).
- **Core test:** the foundation runs the core API flows and screen copy twice, with the launch packs and with the fixture packs. Any word or rule that comes out the same under both, when it should come from a pack, fails the build. This is the main guard for rule 11.
- **Pack lint (CI):** every pack validates against `packs/schema/`; jurisdiction files contain no specialty words; specialty files contain no country words; placeholders resolve; every registry key has an i18n label.
- **Slice code scan (CI):** the forbidden-word list in 3.5 is scanned in `api/app/features`, `web/src/capabilities` and prompt templates.

### 3.7 What each layer must provide

| Layer | Required | Optional |
|---|---|---|
| Jurisdiction | code, name, version, cell, locale, timezone, date_order, currency, holiday source, registration bodies by profession, retention years, quarantine purge days, vocabulary for party roles and instructing-party types and the instructing-party request wording, declarations, deadlines (with calendar rule), report compliance items, identifier recognisers | court tracks, written-question classifications, meeting types, legal tests, final checks |
| Case type | code, name, version, expert roles, party roles, instructing-party types, workflow steps and screens, question types, report sections | case-type declarations, final checks, extra registries |
| Specialty | code, name, version, profession, fact kinds (with field schemas), source types, lanes | checks, final checks, vocabulary |
| Overlay | — | record systems and cues, guideline sources, local vocabulary (clinician title), speech phrases, drug monitoring, unit display rules |

---

## 4. Data model (Supabase Postgres)

Grouped by area (4.1–4.11), in the order a case moves through them; the data dictionary uses the same groups. `id` is a UUID on every table; all case data carries `case_id` and is covered by row-level security. **In plain words** says what each table is for and how it links to others; every field is explained in plain English in `docs/spec/data-dictionary.md`. Behaviour (who can do what, lifecycles, leaving) is in section 10 and `docs/spec/behaviour-index.md`.

### 4.1 People

Who signs in, and the professional profile of those who give evidence.

| Table | In plain words | Key fields |
|---|---|---|
| `people` | One row per person who can sign in (an expert, an assistant, an agency admin). Their login lives in Supabase Auth; this is their Avodeck profile. | id (= auth user id), full_name, email, locale, timezone, home_region, records_custodian (name, email), created_at, closed_at, email_bounced_at |
| `experts` | The professional side of a person who gives expert evidence: profession, specialties and registration. Not every person is an expert (assistants are not). One per person. | person_id, profession, specialties[], registration_body, registration_number, hourly_rate, currency, default_retention, default_jurisdiction |
| `expert_roles` | The clinical jobs an expert has held and when (e.g. GP partner 2005–now). Used to check they practised in the relevant setting and period. | expert_id, specialty, role_title, setting, period |
| `expert_training` | Courses and qualifications listed on the expert's CV. | expert_id, title, year |
| `report_templates` | A Word file the expert's reports are built into. Can belong to a person or an organisation. | owner_type (person/organisation), owner_id, storage_path, sha256, style_map, heading_map, version |
| `declarations` | Any extra declaration wording the expert adds; the court's fixed wording comes from the pack. | expert_id, jurisdiction, kind, text, locked_at |
| `signatures` | The expert's signature image. | expert_id, storage_path, method, created_at |
| `sessions` | Each device a person is signed in on, so they can see and sign out devices. The cookie holds a random token; only its scrambled copy is kept, and Supabase Auth's tokens stay here, never in the browser. | person_id, token_hash, csrf_hash, device_label, aal, mfa_verified_at, created_at, last_seen_at, revoked_at, revoked_reason |
| `mfa_recovery_codes` | One-time backup codes for when someone loses their phone (stored scrambled). | person_id, code_hash, used_at |
| `mfa_attempts` | Wrong authenticator codes in the last 15 minutes (5 lock MFA for the rest of the window) and the last code accepted, so it cannot be used twice. | person_id, failures, window_started_at, last_code_hash, last_code_at |
| `email_changes` | A change of sign-in email waiting for both the old and the new address to confirm (links last a day). | person_id, new_email, old_token_hash, new_token_hash, old_confirmed_at, new_confirmed_at, completed_at, cancelled_at, expires_at |

### 4.2 Organisations

Who owns cases and pays, who belongs, and how they want to be told about things.

| Table | In plain words | Key fields |
|---|---|---|
| `organisations` | The account that owns cases and pays: a person's own practice or a team (agency, chambers, NHS team). Every case belongs to exactly one. | name, kind (personal/team), region, billing_profile_id, policy (required template, retention limits), created_at, closed_at |
| `memberships` | Who belongs to which organisation and in what role. One person can be in several organisations; leaving ends the membership but keeps the history. | organisation_id, person_id, role (owner/admin/expert/assistant/viewer), started_at, ended_at |
| `invites` | An invitation to join an organisation, sent by email, valid for 7 days. | organisation_id, email, role, invited_by, expires_at, accepted_at |
| `billing_profiles` | Who an invoice is from: name, address and tax number of the organisation or expert issuing it, with their own invoice numbering. | owner_type, owner_id, legal_name, address, tax_number, currency, next_invoice_no |
| `notification_preferences` | How a person wants to be told about things, per organisation (in-app, email, daily digest). | person_id, organisation_id, channel, enabled, digest, muted_kinds[] |
| `support_access_grants` | A time-limited permission the named expert gives Avodeck support to look at one case. | case_id, granted_by, granted_to, reason, expires_at, revoked_at |

### 4.3 Case

One instruction: who it's for, who's involved, what was asked.

| Table | In plain words | Key fields |
|---|---|---|
| `cases` | One instruction to give expert evidence. Belongs to one organisation and has one named expert whose evidence it is. Everything else in a case hangs off this. | organisation_id, named_expert_id, jurisdiction, case_type, specialty, expert_role, pack_versions, label ("Mrs A"), subject_status (living/deceased/minor), party_role, court_track, due_date, period_start, period_end, stage, status, on_hold, retention_policy, delete_after, settled_at, closed_at |
| `case_members` | Other people added to a case (assistant, supervisor, co-expert, viewer). They can help or view; only the named expert signs and verifies. | case_id, person_id, case_role, added_by, added_at, removed_at |
| `case_parties` | The people and organisations on each side of the claim (e.g. the claimant, a GP and a hospital trust). | case_id, role_key, name, representative (name and representative encrypted under the case key) |
| `case_instructing_parties` | Who instructed the expert (usually one firm; two for a joint expert), with dates if the firm changes. | case_id, type_key, name, firm, email, reference, effective_from, effective_to, replaces_id (joint = two or more current rows; no stored flag) |
| `case_links` | Connections between cases (a related inquest, a previous instruction, a case this one replaces). Links never share data. | case_id, linked_case_id, kind |
| `instruction_versions` | Each letter of instruction or set of oral instructions, in order. Questions belong to one of these. Foundation creates the table (migration 0019); slice C fills it. | case_id, version_no, kind (pack key: letter/supplementary/oral), document_id, received_on, locked_at, locked_by (oral wording is in `case_instructions`) |
| `case_instructions` | The words of oral instructions, one record per oral instruction version and date received, typed or from dictation. Slice C. | case_id, instruction_version_id, text (encrypted), source (typed/dictated), transcript_id, received_on |
| `case_subject_keys` | A keyed hash of the subject's identifier, for the duplicate-case warning. The identifier itself is never stored. Slice C. | case_id, key_hash |
| `conflict_answers` | The expert's answers to the conflict-of-interest questions (keys from the jurisdiction pack's `conflicts.yaml`), which feed their declaration. Slice C. | case_id, question_key, answer (yes/no), detail (encrypted; only with yes), answered_by, version, created_at, updated_at |
| `conflict_answer_history` | Every conflict answer as it was given or changed, written only by a database trigger; append-only. The answers behind a declaration are the latest rows before it; a newer row means the declaration is out of date. Slice C. | case_id, question_key, answer (yes/no), detail (encrypted), answered_by, created_at, recorded_at |
| `conflict_declarations` | Each time the expert declares conflicts (at the start, or mid-case). Append-only; the latest is current. Slice C. | case_id, outcome (none/disclosed), declared_by, created_at |
| `case_withdrawals` | Why the expert withdrew from the case (one per case; the case moves to `withdrawn` in the same transaction). Slice C. | case_id, reason (encrypted), withdrawn_by, created_at |
| `questions` | The questions the expert has been asked to answer, word for word from the instructions. | case_id, instruction_version_id, number, text_verbatim, source_word_ids[], para_ref, kind, outside_expertise, answer_parts[], outside_expertise_source, status, confirmed_text |
| `assumptions` | Things the instructions tell the expert to assume. | case_id, instruction_version_id, number, text_verbatim, source_word_ids[], para_ref, status, confirmed_text |
| `instruction_extractions` | One record per letter read by `extract_questions`: counts kept and refused, whether it reopened the set. Counts only. Slice C. | instruction_version_id, case_id, job_id, questions, assumptions, rejected, reopened, model_task, prompt_version |
| `prefill_suggestions` | Case details T-04 suggested from the letter, each citing its words, waiting for the expert to accept or dismiss. Never applied to the case by the server. Slice C. | case_id, instruction_version_id, field, value (encrypted), word_ids[], status (suggested/accepted/dismissed), decided_by, decided_at, model_task, prompt_version |
| `prefill_runs` | One record per `prefill_case` run: suggestions kept and refused. Counts only. Slice C. | case_id, job_id, kept, rejected |

### 4.4 Bundle

The records received, every page and word, and the facts found on them.

| Table | In plain words | Key fields |
|---|---|---|
| `documents` | Each file received (records, instructions, witness statements, court bundles…), with who sent it and when. Split into pages. | case_id, kind, filename, storage_path, sha256, pages, received_on, obtained_how, bundle_version |
| `document_scans` | The safety scan of each received file, what kind of file it turned out to be, and when it was split into pages. | document_id, case_id, clean, signature, media_type, scanned_at, split_at |
| `document_parts` | Which files came inside another file (an email's attachments, a zip's files), in order. Each part is a document of its own. | case_id, parent_document_id, document_id, part_no, media_type |
| `exhibits` | Files that can't be paged, such as audio, video or X-ray images; listed but not read by Avodeck. | case_id, document_id, media_type, size, storage_path, sha256, note |
| `pages` | Every page of every document, with its permanent page number. Facts and reads point at pages. | case_id, document_id, bundle_page_no, source_type, image_path, text_source, ocr_confidence, handwriting, rotation, duplicate_of_page_id, superseded_by_page_id, quarantined (reason, by, at), unreadable, needs_clearer_copy, relevance, language, transcribed_by_expert_text |
| `page_analysis` | The worker's working notes on each page: how far it got, the fingerprints used to find duplicates, printed dates, the sealed extraction and whether a person must check it. | page_id, case_id, stage, word_count, typed_words, phash, minhash, printed_dates, availability_evidence, extraction (sealed), needs_human, reason, model_id, duplicate_status, duplicate_version, references_checked_at |
| `page_embeddings` | One search vector per page, made from the page's words after identifiers are stripped, with the embedder that made it; a page embedded by an older model is embedded again. | page_id, case_id, model, embedding, created_at, updated_at |
| `page_quarantines` | Pages that may belong to someone else: suggested, confirmed by the named expert or undone; once the instructing party is told or the pack's days pass, the page's image and text are deleted and it keeps its number. | case_id, page_id, status, reason, suggested_by, confirmed_by, confirmed_at, closed_by, closed_at, purged_at, prior_facts, version |
| `page_replacements` | A clearer copy of a page: the later document that replaces it and, once split, the new page that supersedes it. | case_id, page_id, document_id, new_page_id, requested_by, linked_at |
| `ocr_words` | Every word read on a page, with where it sits. Facts cite these words, which is how every fact points to its exact place. | page_id, word_index, text, polygon, confidence, source (ocr/embedded/expert) |
| `facts` | A single thing found on a page (a consultation, a result, a prescription…), linked to the exact words it came from. Suggested by the computer, then checked by the expert. | case_id, page_id, kind, fields, value, word_ids[], bbox, event_date, date_basis, available_at, availability_basis, status, version, model_task, model_id, prompt_version |
| `fact_verifications` | The history of every check the expert made on a fact (correct, not right, changed). Never edited. | case_id, fact_id, person_id, seq, action (correct/not_right/edit/move/remapped; `remapped` rows come from the reading job with no person), old_value, new_value, old_fields, new_fields, old_word_ids, new_word_ids, old_page_id, new_page_id, reason, at, prev_hash, hash |
| `page_reads` | Proof the expert read each page: who, when, how long. Never edited; each row is chained to the last so tampering shows. | case_id, page_id, person_id, seq, at, dwell_seconds, counts_for_citing, prev_hash, hash |
| `fact_flags` | Facts the expert marked to come back to under "points against" (`points`), and checked facts the reading job flagged "Please check" after a page was read again (`recheck`, no person). | case_id, fact_id, person_id, flagged_for, at |
| `missing_items` | Documents mentioned in the records but not in the bundle, to ask for. | case_id, description (sealed), citing_page_id, reference_key, word_ids, reference_date, status, reason (sealed), received_page_id, version |
| `record_gaps` | Periods where a record source seems to go quiet (possible missing records). | case_id, source_type, fact_kind, gap_start, gap_end, last_page_id, confirmed, version |
| `instructing_party_requests` | Emails asking the instructing party for missing or clearer records, and when they were sent. | case_id, items, body_text, sent_via, sent_at, message_id, follow_up_on, quarantined_page_ids |
| `case_processing` | The expert's controls over reading the bundle: a request to stop at the next page, and the opt-in to read it again with a newer model. | case_id, cancel_requested_at, cancel_requested_by, reprocess_opted_at, reprocess_opted_by |

### 4.5 Understand

How facts relate to the questions, and what the checks found.

| Table | In plain words | Key fields |
|---|---|---|
| `fact_question_links` | Which facts relate to which question (suggested by the computer, confirmed by the expert). | fact_id, question_id, status, reason |
| `check_config` | The expert's on/off choices for "Things to look at" checks, any window they changed, and the checks they add themselves (a name and a "not followed up" rule). | person_id, check_key, enabled, window_days, label, custom |
| `findings` | Things a check found for the expert to decide on, with the evidence pages. | case_id, check_key, rule, gentle, evidence_fact_ids[], evidence_page_ids[], status (open, gone), decision, reason, run_id |
| `check_runs` | Each time the checks ran on a case, with the pack version and which checks ran. | case_id, job_id, pack_version, checks |
| `case_guideline_versions` | Which guideline version a case was shown, so a later change to the guidance table shows a notice. | case_id, code, version_label, table_reviewed_at, entry_hash |
| `availability_overrides` | The expert's corrections to when an item became available to the clinician. | case_id, fact_id, available_at, unknown, reason, at |
| `consultation_notes` | Each person's private note on a consultation (only its author sees it). | case_id, fact_id, person_id, text |
| `guideline_versions` | Which version of a guideline was in force on which dates (shared, not patient data). Not a database table: read from the overlay pack's `guidelines.yaml` with `pack.guidelines()` (ADR 0016). | jurisdiction, specialty, source, guideline_code, title, version_label, in_force_from, in_force_to, url, licence, table_version, reviewed_at |
| `comments` | Notes between people on a case (e.g. a supervisor's comment on a paragraph). Never in reports. | case_id, author_id, target_type, target_id, text, at, resolved_at |

### 4.6 Write

The expert's own reasoning and words.

| Table | In plain words | Key fields |
|---|---|---|
| `points` | Facts sorted for or against, for each question. | case_id, question_id, fact_id, side, status, reason |
| `range_notes` | The expert's note on what other reasonable clinicians might have done. | case_id, question_id, text |
| `relevant_facts` | Checked facts the draft does not use yet that may bear on it (T-14b), until used or dismissed. | case_id, question_id, fact_id, why_relevant, status |
| `witness_claims` | Statements in a witness statement compared with the records. | case_id, document_id, text_verbatim, word_ids[], verdict, fact_ids[], status |
| `witness_comparisons` | Each request to compare a witness statement with the records (T-14c job). | case_id, document_id, job_id, status, claims |
| `opinions` | The expert's own words answering each question, with citations to checked facts. | case_id, question_id, body, citation_refs, view, provisional, test_text, updated_at, etag |
| `opinion_versions` | Every saved version of an opinion, so earlier wording can be seen or restored. | opinion_id, version_no, body, citation_refs, test_text, saved_at, saved_by |
| `lint_issues` | Gentle warnings about wording (uncited facts, loaded words, outside expertise) and why the expert dismissed them. | case_id, opinion_id or paragraph_id, sentence_index, type, words, dismissed_reason |

### 4.7 Report

Built reports, what was frozen at signing, and who received them.

| Table | In plain words | Key fields |
|---|---|---|
| `report_versions` | Each built report (draft, served, supplementary, answers). Once signed it can never change. | case_id, version_no, type, display_name, docx_path, pdf_path, sha256, locked_at, withdrawn_at, withdrawn_reason, prev_version_id |
| `report_snapshots` | Frozen copies taken at signing (CV, declarations, signature, template, pack and guideline versions, instructions, conflicts) so later changes never alter a served report. | report_version_id, kind, content_ref, sha256 |
| `report_paragraphs` | The paragraphs of a built report, so changes can be traced. | report_version_id, para_no, section, content_control_id, text, question_id |
| `citations` | Each page reference in a report, with a frozen copy of the fact as it was when cited. | paragraph_id, fact_id, fact_version, value_snapshot, quote_verbatim, page_id |
| `report_servings` | Who each served document was sent to, when and how (several recipients for a joint expert). | report_version_id, recipient_party_id, served_on, sent_via |
| `final_check_runs` | Results of the final check before signing. | case_id, report_version_id, results, passed, at |
| `spot_checks` | A colleague's check of a sample of cited facts through an expiring link. | case_id, reviewer_email, fact_ids[], token_hash, code_hash, code_attempts, expires_at, responses |
| `spot_check_address_tries` | How many times one address has tried any spot-check link in the last 15 minutes (20 are allowed); the address kept only as a hash, the row deleted after a day. | address_hash, tries, window_started_at |
| `report_settings` | The case's report choices: citation style and the declaration version the expert acknowledged. | case_id, citation_style, acknowledged_declaration_version |
| `report_section_texts` | Sections the expert writes themselves (outside the opinions), sealed with the case key. | case_id, section, body, citation_refs, version |
| `report_builds` | How each draft was built: citation style, template or fallback, page count and pack versions. | report_version_id, job_id, citation_style, template_id, template_fallback, page_count, pack_versions |

### 4.8 Money

Time, invoices and corrections.

| Table | In plain words | Key fields |
|---|---|---|
| `time_entries` | Time the expert spent, by stage, for invoicing. | case_id, person_id, stage, started_at, ended_at, seconds, adjusted_by_expert |
| `time_estimates` | The expert's time estimate per workflow step; a change is a new row, the newest counts; never changed or deleted. | case_id, stage, hours, fee (optional fixed fee), created_by, created_at |
| `invoices` | Invoices sent to instructing parties; never changed once sent. | case_id, billing_profile_id, number, lines, total, currency, tax, tax_rate, issuer (with bank details), statements (the pack's invoice lines) and estimate_amount (snapshots when drafted), status (draft/sent/part_paid/paid/written_off), paid_amount (sum of payments), pdf_path, sent_at |
| `invoice_payments` | Each payment received on a sent invoice; never changed or deleted. No case_id: seen and recorded through its invoice's case, and goes with the invoice (cascade). | invoice_id, amount (> 0), recorded_by, recorded_at |
| `credit_notes` | Corrections to a sent invoice. | invoice_id, number, amount, reason, issued_at |

### 4.9 Court

Everything after serving: deadlines, hearings, questions, meetings and court bundles.

| Table | In plain words | Key fields |
|---|---|---|
| `court_directions` | Orders from the court that set or change deadlines and hearings. | case_id, made_on, reference, text, created_deadline_ids[] |
| `deadlines` | Every date that matters (questions due, meeting, trial), worked out from the pack or a court order. | case_id, kind, rule_ref, due_on, calendar, source (pack/direction/manual), pack_version, done_at, cancelled_at |
| `deadline_changes` | Every time a deadline moved and why (extension, new order). | deadline_id, old_due_on, new_due_on, reason, source_id, at |
| `hearings` | Trials and other hearings, with adjournments kept as history. | case_id, type, starts_on, ends_on, venue, status, replaced_by_hearing_id |
| `written_question_rounds` | Each batch of written questions from a party, with its deadline. | case_id, from_party_id, received_on, deadline_id |
| `written_questions` | Each written question and the expert's answer. | case_id, round_id, rule_ref, number, text_verbatim, word_ids[], classification, paragraph_ids[], answer_text, status |
| `opposing_reports` | Each other side's expert report registered for checking, with when it was checked and the positions compared. | case_id, document_id, received_on, job_id, checked_at, claims_found, positions |
| `opposing_claims` | Statements in the other side's expert report checked against the pages they cite. | case_id, document_id, text_verbatim, word_ids[], cited_page_ref, cited_value, status, evidence_word_ids[], expert_confirmed |
| `report_changes` | Corrections or changes of opinion after serving, which become a supplementary report. | case_id, kind, paragraph_id, old_text, new_text, reason, dependent_paragraph_ids[], supplementary_version_id |
| `expert_meetings` | A meeting of experts and the document they sign (e.g. joint statement). | case_id, type, discussion_on, sign_by, send_by, status, signed_document_id, signed_hash |
| `expert_meeting_participants` | Everyone in that meeting (any number, any discipline, on Avodeck or not). | meeting_id, name, discipline, side, person_id (if on Avodeck) |
| `agenda_versions` | Each version of the meeting agenda. | meeting_id, version_no, document_id, received_on |
| `agenda_items` | Each agenda question with each expert's position. | agenda_version_id, number, text_verbatim, own_position, other_positions, agreed, reason, served_paragraph_ids[], deviation_status |
| `court_bundles` | The court's own paginated bundle, in versions as it is re-issued. | case_id, document_id, version, section_prefix, superseded_by_id, pages, received_on |
| `concordance` | Which of our pages is which page in a court bundle version. | case_id, court_bundle_id, page_id, trial_page_no, score, method, confirmed |
| `trial_pack_settings` | What the expert chose to put in the trial pack. | case_id, sections[], include_guidance, hearing_id, requested_job_id, version |
| `trial_packs` | The large-print pack for a hearing; marked stale if new material arrives. | case_id, hearing_id, sections, pdf_path, sha256, built_at, stale |
| `offline_packages` | The encrypted copy for the witness box, per device. | case_id, hearing_id, device_label, format_version, built_at, expires_at, revoked_at |

### 4.10 Close

Stopping and proving deletion.

| Table | In plain words | Key fields |
|---|---|---|
| `legal_holds` | A stop on deletion while litigation or an appeal continues. | case_id, reason, set_by, until, released_at, released_by |
| `deletion_certificates` | Proof of what was deleted and when, sent to the expert and instructing party. Counts, hashes and dates only (rule 6). | case_id, deleted_at, counts, hashes, log_chain_heads, backup_expiry_on, sent_to |
| `deletion_ledger` | A list of deleted case IDs, replayed after any backup restore so deleted cases never come back. | case_id, deleted_at, created_at |

### 4.11 System

The machinery behind everything: keys, jobs, notifications, logs.

| Table | In plain words | Key fields |
|---|---|---|
| `case_keys` | The case's encryption key (scrambled by the key service); destroying it makes the case unreadable. | case_id, wrapped_key, key_ref, created_at, destroyed_at |
| `jobs` | Background work (processing a bundle, building a report) and its progress. | case_id, kind, status, priority, attempts, step, pct, eta_seconds, step_done, step_total, code_version, error_code, requested_by |
| `job_steps` | The steps of a job that have finished, so a retried or requeued job skips them (F6). | job_id, case_id, step, created_at |
| `job_kind_actions` | The case action each job kind needs; `app.enqueue_job()` looks it up, so a caller cannot choose a weaker one. | kind, action |
| `scheduled_runs` | One row per daily job per date it ran, so a missed date catches up exactly once (F28). | name, run_date, started_at |
| `uploads` | A file being sent in 8 MB chunks: which chunks have arrived, and the document it became. | case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count, received, status, document_id, append, expires_at, completed_at |
| `notifications` | Messages shown in the app or emailed (never with patient details). | person_id, organisation_id, case_id, kind, in_app, created_at, read_at, emailed_at, held_at |
| `feature_flags` | Switches that turn features on for everyone, a region, an organisation or a person. | key, scope, scope_id, enabled |
| `audit_log` | Every important action: who, for which organisation, on which case, what. Chained so tampering shows. Kept as metadata after a case is deleted. | person_id, organisation_id, case_id, action, target, at, prev_hash, hash |
| `idempotency_keys` | The first answer to a request sent with an `Idempotency-Key`, kept for 24 hours so a repeat (double click, retry) does the work once (F27). | person_id, key, case_id, fingerprint, status_code, response_body (sealed), created_at, expires_at |
| `ai_calls` | A record of each AI call (which model, which prompt version, how many tokens), never the content. | case_id, person_id, task_id, model_id, region, prompt_version, input_tokens, output_tokens, outcome, at |
| `transcripts` | Finished dictation text the expert kept (encrypted under the case's key). | case_id, person_id, context, text, duration_seconds |
| `may_matter` | New records after serving that might affect an answer, for the expert to look at. | case_id, fact_id, question_id, why, seen_at |


**How the main pieces connect:** a **person** has **memberships** in **organisations** and may be an **expert**. An organisation owns **cases**; each case has one **named expert** and optional **case members**. A case receives **documents**, split into **pages**; **OCR words** sit on pages; **facts** cite those words. The expert's **page reads** and **fact verifications** make facts citable. **Opinions** cite facts through **citations**; a **report version** gathers them and, once signed, is frozen with its **snapshots** and **servings**. Court work (**directions, deadlines, hearings, written questions, meetings, court bundles**) hangs off the case. **Legal holds**, **deletion certificates** and the **deletion ledger** govern how it ends.

---

## 5. API

### 5.1 Conventions

- **Base path:** `https://{region}.api.avodeck.com/v1` (UK first: `uk.api.avodeck.com`), served by FastAPI on DigitalOcean App Platform in that region. Every regional cell runs the same code and API.
- **Server-side by default:** the browser only talks to FastAPI. It never calls Supabase, Azure or AWS directly, holds no Supabase key, and never gets a direct storage link. Only two things run on the device because they must: drawing the signature on screen, and the offline witness-box search (screen 25).
- **Auth:** the browser posts credentials to FastAPI; FastAPI calls Supabase Auth server-side and sets the session as an `httpOnly`, `Secure`, `SameSite=Strict` cookie that page scripts cannot read. FastAPI keeps the Supabase access and refresh tokens server-side (or encrypted inside the cookie), refreshes them itself, and verifies every token against Supabase's signing keys (JWKS), checking `aud`, `exp`, `sub` and `aal`.
- **CSRF:** because a cookie is sent automatically, every state-changing request also carries a CSRF token (double-submit) and the API only accepts requests from the app's own origin.
- **Sessions:** 30-minute inactivity timeout, maximum session length, sign-in and failure events written to `audit_log`.
- **Step-up:** endpoints marked 🔒 require `aal2` (MFA completed in the last 10 minutes): sign, send, export, delete, retention changes.
- **Access:** every `/cases/{case_id}/…` route calls `require_case_access(action)` (section 10.1, `docs/spec/permissions.md`): the named expert, a case member whose case role allows the action, or an organisation owner/admin for administrative actions. Anyone else gets 404. Supabase row-level security mirrors the check in the database. `GET /cases/{case_id}` returns `allowed_actions` for the signed-in person so screens can hide what they can't do; the server still decides.
- **Concurrency:** every editable resource returns an `ETag`; updates must send `If-Match` and get 412 `stale` if someone else changed it first.
- **Idempotency:** create, send, serve, sign and pay-style requests accept an `Idempotency-Key` header; a repeat within 24 hours returns the first result.
- **Long work:** returns `202 Accepted` + `{job_id}`. Progress comes from `GET /jobs/{job_id}` or a server-sent events stream `GET /jobs/{job_id}/events` (status, step, pct, eta_seconds, step_done and step_total as whole numbers: "page 40 of 900"). No patient text in job rows.
- **Files:** all go through FastAPI. Uploads are sent in 8 MB chunks (`PUT /uploads/{upload_id}/chunks/{n}`) and streamed to Supabase Storage, so large bundles never sit in memory. Page images, thumbnails and downloads are streamed back by FastAPI after the ownership check, with `Cache-Control: private, no-store`.
- **AI task IDs** (T-xx) refer to section 6.
- **Errors:** `{error: {code, message}}`. 409 for state conflicts (e.g. editing a locked report; `already_confirmed` and `questions_locked` for questions), 423 for blind-read gating.

### 5.2 Endpoints in journey order

**01 Sign in**

All handled by FastAPI, which calls Supabase Auth server-side. The browser never sees a Supabase token.

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/auth/sign-in` | `{email, password, captcha}` → Supabase Auth → sets httpOnly session cookie | Rate-limited; returns `mfa_required` if MFA not yet completed |
| POST | `/auth/mfa/enroll` | Starts TOTP enrolment; returns QR code | First login |
| POST | `/auth/mfa/verify` | `{code}` → session upgraded to `aal2` | Also used for step-up before 🔒 actions |
| POST | `/auth/sign-out` | Revokes Supabase session, clears cookie | |
| POST | `/auth/password/forgot` | `{email}` → Supabase sends reset email via Resend (custom SMTP) | Same response whether or not the email exists |
| POST | `/auth/password/reset` | `{token, new_password}` → leaked-password check → all sessions revoked | |
| GET | `/cell` | Public: `{support_phone, support_hours, storage_place}` from the cell's environment, null when unset (sign-in screen) | |
| GET | `/auth/session` | Is the session valid, MFA level, minutes until timeout | |
| GET | `/me` | Person record, expert profile (if any), memberships and whether MFA is enrolled | Creates the `people` row and the personal organisation on first call |
| POST | `/me/sessions/revoke-all` | Signs out all devices | 🔒 |

**02 Expert profile**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET / PATCH | `/me/profile` | Name, profession, specialties, registration body and number (options from the packs), timezone, rate, default retention | |
| GET / POST | `/me/roles` | List / add roles with date ranges | |
| PATCH / DELETE | `/me/roles/{role_id}` | Edit / remove a role | |
| GET / POST / DELETE | `/me/training`, `/me/training/{id}` | Training records | |
| POST | `/me/template/file` | Upload the .docx through the API (multipart) | |
| POST | `/me/template` | Register uploaded template → parse styles; optional T-02 mapping | Returns heading map to confirm |
| PATCH | `/me/template/mapping` | Expert confirms heading map | |
| GET / PUT | `/me/declaration` | Declaration text | Locked after first case is served |
| GET | `/jurisdictions` | Jurisdictions available in this region (UK cell: GB-EAW) | |
| GET | `/jurisdictions/{code}` | Pack summary: vocabulary, declarations and statement-of-truth wording, compliance checklist, deadline rules, legal-test text | Read-only; replaces `/statement-of-truth` |
| GET | `/me/signature/image` | Signature preview, streamed by the API | |
| POST | `/me/signature` | Upload the drawn (PNG from the on-screen pad) or photographed signature | Background removed in worker; only image drawing happens in the browser |

**03 Today**

| Method | Path | Does |
|---|---|---|
| GET | `/today` | "Things that need you" (ranked), case cards with stage and progress, reading-time estimates |
| GET | `/me/stats/balance?months=12` | Counts by party role (labels from the pack) |

**04 New case**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/packs` | Pack combinations available in this cell (launch: gb-eaw · clin-neg-breach · general-practice) | |
| POST | `/cases` | Create draft case (jurisdiction, case type, specialty, label, party role, instructing party type and name, due date, optional court track) | Pack versions stamped on the case |
| GET | `/cases/{case_id}/pack` | Merged pack for this case: vocabulary, workflow steps and screens, lanes, question types, deadlines, declarations | Drives the case header and wording |
| PATCH | `/cases/{case_id}` | Update case details | |
| POST | `/cases/{case_id}/uploads` | Start an upload `{kind, filename, size, sha256, append?}` → `upload_id`; `append` marks later records | |
| PUT | `/uploads/{upload_id}/chunks/{n}` | Send an 8 MB chunk; FastAPI streams it to Supabase Storage | Resumable: missing chunks can be re-sent |
| GET | `/uploads/{upload_id}` | Which chunks have arrived and which are missing, to resume | |
| POST | `/uploads/{upload_id}/complete` | Check sha256, register the document, queue virus scan | 409 `chunks_missing` lists the missing chunks; 422 `sha256_mismatch`. Publishes `DocumentUploaded(case_id, document_id, appended)` in the same transaction; slice A's subscriber queues the virus scan (`scan_document`) and processing, and if it fails nothing is registered |
| GET | `/cases/{case_id}/documents/{document_id}/file` | Stream the stored file back through FastAPI | `Cache-Control: private, no-store` |
| POST | `/cases/{case_id}/prefill` | T-04 suggest fields from the letter | 202 + job; 409 without a letter |
| GET | `/cases/{case_id}/prefill` | Suggestions with their word ids, whether the letter is still being read, how many were refused | |
| PATCH | `/cases/{case_id}/prefill/{suggestion_id}` | Accept or dismiss one suggestion `{decision}` (once; 409 after) | Records the choice only; the case changes when the expert saves the form |
| PUT | `/cases/{case_id}/oral-instructions` | Save oral instructions text | |
| WS | `/cases/{case_id}/dictation?context=` | Browser streams microphone audio to FastAPI over a WebSocket; FastAPI relays it to Azure Speech (UK South) and streams the text back | Used by every dictation box (T-DICT); no Azure key in the browser; audio not stored |
| POST | `/cases/{case_id}/transcripts` | Save the finished, edited transcript `{context, text, duration}` | |
| POST | `/cases/{case_id}/process` | Start the bundle pipeline (screen 07) | 202 + job |
| GET | `/cases/{case_id}/process` | The case's latest processing job (scan or bundle), for screen 07 to follow | `{job: null}` when nothing has run |

**05 Conflicts**

| Method | Path | Does |
|---|---|---|
| GET / PUT | `/cases/{case_id}/conflicts` | Read / save answers; returns generated declaration text and any practice-period warning (coverage of the case period by the expert's roles in the specialty's counted settings: covered/partial/none/unknown); `changed_since_declared` when an answer changed after the latest declaration |
| POST | `/cases/{case_id}/conflicts/declare` | Record the declaration (all questions answered, else 409); drafts the note to every current instructing party |
| POST | `/cases/{case_id}/withdraw` | 🔒 `{reason}` → case `withdrawn`; drafts the note | |

**06 What you've been asked**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/questions` | Questions + assumptions with LOI highlights | Filled by T-06 during processing |
| PATCH | `/cases/{case_id}/questions/{q_id}` | Confirm / edit wording / change kind | |
| PATCH | `/cases/{case_id}/assumptions/{a_id}` | Confirm / edit | |
| POST | `/cases/{case_id}/questions/confirm-all` | Lock the question set | Required before writing |
| POST | `/cases/{case_id}/questions/read-again` | Queue `extract_questions` again after a failed reading (202 + job; 409 if nothing is waiting to be read or a reading is under way) | `case.upload` |
| POST | `/cases/{case_id}/instructing-party-notes/draft` | Template note (e.g. outside expertise), wording from the pack | Returns text to edit |

**07 Preparing your bundle**

| Method | Path | Does |
|---|---|---|
| GET | `/jobs/{job_id}` | Status, step (split/ocr/sort/duplicates/missing), pct, eta |
| GET | `/jobs/{job_id}/events` | Same, as a live server-sent events stream |
| | *(internal worker steps)* | split → T-07a OCR → M9 → T-07b → dates, duplicates, gaps → T-07c → index (M7) → email via Resend |

**08 Bundle ready**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/bundle/summary` | Page counts by source, duplicates, unreadable count, missing items, gaps, provenance |
| GET | `/cases/{case_id}/bundle/sorted` | Pages grouped by the pack's sources, then by the date printed on each page (never from facts); every page keeps its own number; quarantined pages left out |
| GET | `/cases/{case_id}/duplicates` | Pairs to review; thumbnails via `/pages/{page_no}/thumbnail` |
| PATCH | `/cases/{case_id}/pages/{page_id}/duplicate` | Confirm or undo "copy of page X" |
| PATCH | `/cases/{case_id}/documents/{doc_id}` | Edit provenance (how obtained, received date) |
| GET | `/cases/{case_id}/missing-items` | Documents pages mention that the bundle does not hold, with the page that mentions each |
| PATCH | `/cases/{case_id}/missing-items/{item_id}` | Not available (reason), open again, or mark received (a page added later); If-Match |

**08b Ask the instructing party** (pack label: "Ask the solicitor")

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/instructing-party-requests/draft` | Build email from open missing items + unreadable pages (+ optional extension line) | Identifiers stripped |
| POST | `/cases/{case_id}/instructing-party-requests` | Log the final text; MVP returns a `mailto:` link | |
| POST | `/cases/{case_id}/instructing-party-requests/{req_id}/send` | Send via Gmail (`gmail.send`) or Microsoft Graph (`Mail.Send`) | Phase 2; 🔒 |
| GET / DELETE | `/me/mail-connection`, `/me/mail-connection/{provider}/oauth-callback` | Connect / disconnect mailbox | Phase 2 |
| POST | `/cases/{case_id}/documents/append` | Register later records (uploaded via `/uploads`); numbered after the last page | Starts pipeline on new pages only |

**09 Reader**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/pages?cursor=&filter=unread|period|source` | Page list for navigation + progress | |
| GET | `/cases/{case_id}/pages/{page_no}` | Source, period-in-question flag, read state | Facts **not** included |
| GET | `/cases/{case_id}/pages/{page_no}/image?zoom=` | Page image streamed by the API | `no-store` |
| GET | `/cases/{case_id}/pages/{page_no}/thumbnail` | Small image streamed by the API | |
| POST | `/cases/{case_id}/pages/{page_no}/read` | Record read `{dwell_seconds}` → appends to hash-chained log | Unlocks suggestions |
| GET | `/cases/{case_id}/pages/{page_no}/facts` | Suggested + verified facts with bounding boxes | **423** until the page is read |
| GET | `/cases/{case_id}/pages/{page_no}/words` | The page's words (`word_id`, text, box as a share of the page; null for typed words), for choosing the words a changed fact cites | **423** until the page is read; `private, no-store` |
| POST | `/cases/{case_id}/facts/{fact_id}/verify` | `{action: correct|not_right|edit, new_value?, word_ids?, reason?, fields?, event_date?}` | If-Match; append-only history; edit must be in the cited words (422 `invalid`); a "Please check" change needs chosen words (422 `words_needed`); `fields` only the pack kind's fields and types (422 `invalid_fields`) |
| POST / DELETE | `/cases/{case_id}/facts/{fact_id}/flag-point` | Send to Points against, or take it back | Checked facts only (409); E reads `core/contracts/fact_flags.py` |
| POST | `/cases/{case_id}/facts/{fact_id}/move` | `{to_page_no, word_ids}`: move a fact to the clearer copy of its page | If-Match; other pages 409; clearer copy read first (423) |
| GET | `/cases/{case_id}/progress` | Pages read / total, time estimate | |

**10 Pages we couldn't read**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/pages/unreadable` | Queue with done count (done: typed out or clearer copy asked for) | |
| PUT | `/cases/{case_id}/pages/{page_no}/transcription` | `{text, relevance?}`: save expert's text; counts as read; re-runs T-07b on it through `extract_page_text` (core/contracts/pipeline.py) | 202 + job; 503 while A's entry point is missing |
| GET | `/cases/{case_id}/pages/{page_no}/second-reading` | T-10 comparison | Only after a transcription exists, or on request |
| PATCH | `/cases/{case_id}/pages/{page_no}` | `{relevance?, needs_clearer_copy?}` | Clearer-copy pages feed 08b; copies are set aside with A's `PATCH …/pages/{page_id}/duplicate` |

**11 Timeline**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/timeline?from=&to=&status=&question_id=&only=&as_seen_on=` | Events by lane with verification status and links |
| GET | `/cases/{case_id}/fact-links?status=` | Fact–question links (`stale` when the fact was rejected) |
| POST | `/cases/{case_id}/fact-links/suggest` | Run T-11 (202 + job) |
| PATCH | `/cases/{case_id}/fact-links/{link_id}` | Confirm / reject suggested link |
| POST | `/cases/{case_id}/fact-links` | Add a link by hand (checked facts only) |

**12 One consultation, as the clinician saw it**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/consultations/{fact_id}?as_seen=true` | The entry, what was available on that date, what wasn't, later notes (hidden), guidance in force |
| PATCH | `/cases/{case_id}/facts/{fact_id}/availability` | Expert overrides availability `{available_at | unknown, reason}` |
| GET | `/cases/{case_id}/consultations/{fact_id}/later-notes` | Later entries of the same kind, without their words |
| PUT | `/cases/{case_id}/consultations/{fact_id}/note` | Expert's note |
| GET | `/guidelines/in-force?code=&date=&case_id=` | Version label + link from the curated table for the case's jurisdiction and specialty |

**13 Things to look at**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/findings` | Findings with evidence pages and decisions |
| PATCH | `/cases/{case_id}/findings/{finding_id}` | `{decision, reason}` |
| GET / PATCH | `/me/checks` | Checks on/off and "gentle" (applies to new runs) |
| POST | `/cases/{case_id}/findings/rerun` | Re-run checks after config change (202 + job) |
| POST | `/me/checks/custom` | Add custom rule (rule JSON; T-13b later) |

**14 Points against**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/questions/{q_id}/points` | Facts by side + unused relevant facts + accounts that differ |
| POST | `/cases/{case_id}/questions/{q_id}/points` | Add an unused citable fact as a point (starts accepted) |
| POST | `/cases/{case_id}/questions/{q_id}/points/suggest` | T-14a / T-14b (202 + job) |
| PATCH | `/cases/{case_id}/points/{point_id}` | Move side / accept / remove |
| PATCH | `/cases/{case_id}/relevant-facts/{relevant_id}` | Dismiss a fact T-14b suggested |
| PUT | `/cases/{case_id}/questions/{q_id}/range-note` | "What other {clinicians} might reasonably do" (wording from the pack) |
| POST | `/cases/{case_id}/witness-statements/{doc_id}/compare` | T-14c (202 + job) |
| PATCH | `/cases/{case_id}/witness-claims/{claim_id}` | Confirm / own verdict (edited) / dismiss |

**15 Your opinion**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/questions/{q_id}/workspace` | Verified facts as chips, guidance on the day, points summary, current text | |
| PUT | `/cases/{case_id}/questions/{q_id}/opinion` | Save text, citation objects, view, provisional flag | Autosave |
| POST | `/cases/{case_id}/questions/{q_id}/opinion/lint` | T-15a | Real-time, <2 s |
| PATCH | `/cases/{case_id}/lint-issues/{issue_id}` | Dismiss with reason | |

**16 Your report**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/report/build` | Assemble draft in the expert's template | 202 + job → new `report_versions` row |
| GET | `/cases/{case_id}/report` | Latest draft: sections, compliance checklist (from the jurisdiction pack), page count | |
| GET | `/cases/{case_id}/report/preview/{page}` | Rendered report page image, streamed by the API | |
| PATCH | `/cases/{case_id}/report/settings` | Citation style: page / paragraph | Triggers rebuild |
| POST | `/cases/{case_id}/report/word-import` | Upload the edited Word file through the API; returns differences and flags | Phase 2 |
| GET | `/cases/{case_id}/report/methodology` | Generated "how you used Avodeck" appendix | |

**17 Final check**

| Method | Path | Does |
|---|---|---|
| POST | `/cases/{case_id}/report/final-check` | Run 28 checks (26 code + T-17) → `final_check_runs` |
| GET | `/cases/{case_id}/report/final-check/latest` | Results with "fix it" targets |
| POST | `/cases/{case_id}/spot-checks` | Send 10 random cited facts to a colleague (expiring link + email code) |
| GET / POST | `/spot-checks/{token}`, `/spot-checks/{token}/responses` | Colleague's view (code in `X-Spot-Check-Code`) and answers (no login; public and token-gated: the link token and emailed code hashes are the gate, no `require_case_access`; every outcome is 200 with `status` `ok`, `bad_code`, `locked`, `expired` or `unknown`, answers also `already` or `invalid`, and a wrong code commits its count; page crops only) |

**18 Sign and send**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/report/sign` | Apply signature + full name, confirm the pack's declarations, lock, hash | 🔒 409 if final check not passed |
| GET | `/cases/{case_id}/report/versions/{v}/download?format=docx|pdf` | File streamed by the API | Logged in `audit_log` |
| POST | `/cases/{case_id}/report/versions/{v}/serve` | Mark served `{served_on, sent_via}`; creates deadlines | 🔒 |

**18b Time and invoice**

| Method | Path | Does |
|---|---|---|
| POST | `/cases/{case_id}/time/heartbeat` | `{stage}` every 30 s while active (idle ignored) |
| GET | `/cases/{case_id}/time` | Totals by stage vs estimate |
| PATCH | `/cases/{case_id}/time/adjust` | Expert adjustment (audited) |
| POST | `/cases/{case_id}/time/estimates` | Time estimate per workflow step (new rows; audited) |
| POST | `/cases/{case_id}/invoices` | Generate invoice PDF |
| POST | `/cases/{case_id}/invoices/{inv_id}/send` | Email to the instructing party via Resend (attachment, no clinical content); currency and tax from the expert and pack |

**19 After serving**

| Method | Path | Does |
|---|---|---|
| GET | `/cases/{case_id}/deadlines` | Timeline of steps and dates |
| POST / PATCH | `/cases/{case_id}/deadlines`, `/cases/{case_id}/deadlines/{id}` | Add from court directions / mark done |
| GET | `/cases/{case_id}/report/served` | Locked version |

**20 Questions from the other side** (written questions; pack label "Part 35 questions")

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/written-questions/import` | Register uploaded questions document → T-20a, T-20b | 202 + job |
| GET | `/cases/{case_id}/written-questions` | Questions with kind, linked paragraphs, countdown | |
| PATCH | `/cases/{case_id}/written-questions/{q_id}` | Confirm / answer text with citations | |
| POST | `/cases/{case_id}/written-questions/{q_id}/tell-instructing-party` | Draft note for out-of-scope question | |
| POST | `/cases/{case_id}/written-questions/answers/sign` | Lock and sign the answers document | 🔒 uses 18 flow |

**21 New records and the other expert's report**

| Method | Path | Does |
|---|---|---|
| POST | `/cases/{case_id}/documents/append` | New pages (as 08b) → pipeline + T-21a |
| GET | `/cases/{case_id}/new-since-served` | New pages, which may matter (shown only on pages the expert has read) |
| POST | `/cases/{case_id}/new-since-served/check` | Look for new pages that may matter → T-21a (202 + job) |
| POST | `/cases/{case_id}/opposing-reports` | Register report → T-21b, T-21c, T-21d (202 + job) |
| GET | `/cases/{case_id}/opposing-reports/{doc_id}` | Claims with status and evidence; positions side by side |
| PATCH | `/cases/{case_id}/opposing-claims/{claim_id}` | Expert confirms / dismisses discrepancy |

**21b Correct or update your report**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/report-changes` | `{kind, paragraph_id, new_text, reason}` → T-21e dependencies | |
| GET | `/cases/{case_id}/report-changes` | Open changes and the supplementary draft they belong to | |
| GET / PATCH | `/cases/{case_id}/report-changes/{change_id}` | Review dependent paragraphs, edit | |
| POST | `/cases/{case_id}/report-changes/supplementary` | Build supplementary report from open changes | 202 + job |
| POST | `/cases/{case_id}/report/versions/{v}/sign` | Sign and lock the supplementary report | 🔒 |

**22 Expert meeting** (pack label "Joint statement")

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/expert-meetings` | Create `{type, other_expert_name, discussion_on}`; deadlines from the pack's meeting rules | |
| GET | `/cases/{case_id}/expert-meetings` | The case's meetings | |
| GET | `/cases/{case_id}/expert-meetings/types` | Meeting types and their wording from the pack | |
| POST | `/cases/{case_id}/expert-meetings/{meeting_id}/agenda` | Register agenda → T-22a, T-22b | 202 + job |
| GET | `/cases/{case_id}/expert-meetings/{meeting_id}` | Items with positions, agreement, deviation warnings | |
| PATCH | `/cases/{case_id}/agenda-items/{item_id}` | Positions, agreed, reason → T-22c | |
| POST | `/cases/{case_id}/expert-meetings/{meeting_id}/prepare` | Build document with standard declarations | |
| POST | `/cases/{case_id}/expert-meetings/{meeting_id}/send-for-signature` | Dropbox Sign envelope to both experts (MVP: download for counterpart signing) | 🔒 Phase 3 |
| POST | `/webhooks/esign` | Provider callback → status signed, store certificate + hash | Signature-verified webhook |

**23 Court bundle page numbers** (pack label "Trial page numbers")

| Method | Path | Does |
|---|---|---|
| POST | `/cases/{case_id}/court-bundles` | Register the court bundle → T-23 OCR + matching (202 + job) |
| GET | `/cases/{case_id}/concordance?status=low|missing|all` | Old page → trial page with scores; missing pages |
| PATCH | `/cases/{case_id}/concordance/{row_id}` | Confirm or correct a match |
| GET | `/cases/{case_id}/concordance/export?format=pdf|csv` | Print table (🔒 export) |
| POST | `/cases/{case_id}/concordance/tell-instructing-party` | Draft note listing missing pages |

**24 Trial pack**

| Method | Path | Does |
|---|---|---|
| POST | `/cases/{case_id}/trial-pack/check-new` | T-24 nothing-new check on notes |
| PUT | `/cases/{case_id}/trial-pack/settings` | Sections to include |
| GET | `/cases/{case_id}/trial-pack/settings` | Sections chosen so far |
| POST | `/cases/{case_id}/trial-pack` | Build large-print PDF (202 + job) |
| GET | `/cases/{case_id}/trial-pack/latest` | Preview pages |
| GET | `/cases/{case_id}/trial-pack/latest/download` | PDF streamed by the API (🔒 export) |

**25 In the witness box**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/offline-packages` | Build encrypted package `{device_label, expires_at}` | 🔒 |
| GET | `/cases/{case_id}/offline-packages` and `/{pkg_id}` | This case's copies and their state | |
| GET | `/cases/{case_id}/offline-packages/{pkg_id}/status` | The copy's status only, no case content and no step-up; the witness box wipes its copy on revoked, expired or 404 | |
| GET | `/cases/{case_id}/offline-packages/{pkg_id}/download` | Encrypted package streamed once by the API | 🔒 |
| DELETE | `/cases/{case_id}/offline-packages/{pkg_id}` | Revoke (the device wipes its copy at the next online status check) | 🔒 |
| | *(on device, the one unavoidable exception)* | Search runs locally in the browser (SQLite-WASM FTS5) because courtrooms may have no connection; package encrypted, read-only, auto-expires | |

**26 Close the case**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/close` | What screen 26 shows: status, `settled_at`, retention policy, `delete_after`, the pack's retention years, holds, whether deletion has started | |
| POST | `/cases/{case_id}/settle` | Mark settled (no body; the database sets `settled_at`) | |
| POST | `/cases/{case_id}/export` | ZIP of report versions + hashes, methodology, page-read log, invoice, requests (202 + job) | 🔒 |
| GET | `/cases/{case_id}/export/{job_id}/download` | ZIP streamed once by the API | 🔒 |
| PUT | `/cases/{case_id}/retention` | `{policy: delete_now|keep_default|return_then_delete}` → sets `delete_after` (years from the pack) | 🔒 |
| POST | `/cases/{case_id}/archive`, `/cases/{case_id}/reopen` | Archive / reopen | |
| GET | `/cases/{case_id}/deletion-certificate` | The certificate (JSON: counts, hashes, log chain heads, dates, recipient role codes) once deleted; a PDF rendering waits for a PDF library | |
| | *(scheduled job, daily)* | Finds `delete_after <= today` → deletes Storage objects, rows, embeddings, OCR files, packages; destroys case key; writes certificate; emails expert + instructing party | |

### 5.3 Internal and system endpoints

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/health` | Liveness for App Platform | |
| POST | `/webhooks/stripe` | Subscription and payment events | Signature-verified |
| POST | `/webhooks/esign` | E-signature status | Signature-verified |
| GET | `/jobs/{job_id}` | Job status | Anyone who can see the job's case (`case.view_metadata`), else 404 |
| | *Scheduled jobs (App Platform or pg_cron)* | Deadline reminders (daily), deletion run (daily), offline package expiry, guideline table review reminder (quarterly) | Call FastAPI with a service token |
| | *Worker queue (Postgres)* | `process_bundle`, `process_pages`, `suggest_points`, `compare_witness_statement`, `build_report`, `final_check`, `verify_opposing`, `match_trial_bundle`, `build_trial_pack`, `export_case`, `delete_case` | Idempotent per page/step |

### 5.4 Endpoints for accounts, organisations and the behaviour rules

The rules in section 10 need these routes. Owner = the slice that builds them. All `/cases/{case_id}/…` routes go through `require_case_access(action)`; the action name is in `docs/spec/permissions.md`.

**Account and security (foundation)**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/me/sessions` | Devices the person is signed in on | |
| DELETE | `/me/sessions/{session_id}` | Sign out one device | |
| POST | `/auth/mfa/recovery-codes` | Make 10 new one-time recovery codes (old ones stop working) | 🔒 shown once |
| POST | `/auth/mfa/recover` | `{email, password, code}` signs in with a recovery code, removes the old authenticator and asks to re-enrol MFA | Rate-limited |
| POST | `/me/email-change` | Start an email change; confirmation sent to old and new address | 🔒 |
| POST | `/me/email-change/confirm` | `{token}` from each address; changes once both confirm | |
| GET / PUT | `/me/notification-preferences` | Per organisation: in-app, email, digest, muted kinds | |
| GET | `/notifications` | The person's notifications across all organisations | `?unread=true` |
| POST | `/notifications/{notification_id}/read` | Mark read | |
| GET | `/feature-flags` | Flags that apply to this person (server-evaluated) | |

**Organisations and accounts (slice O)**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET / POST | `/organisations` | Organisations I belong to; create a team | Creator becomes owner |
| GET / PATCH | `/organisations/{org_id}` | Name, region, policy | Owner/admin |
| PUT | `/organisations/{org_id}/policy` | Required template, retention limits | Owner, 🔒 |
| GET | `/organisations/{org_id}/members` | Members with role and dates | |
| PATCH / DELETE | `/organisations/{org_id}/members/{membership_id}` | Change role; end membership | Owner/admin, 🔒; last owner cannot leave |
| GET / POST | `/organisations/{org_id}/invites` | List and send invites | Owner/admin, 🔒 |
| DELETE | `/organisations/{org_id}/invites/{invite_id}` | Cancel an invite | |
| POST | `/invites/{token}/accept` | Accept; creates the membership | Signed in as the invited email |
| POST | `/organisations/{org_id}/leave` | Leave; named-expert access to named cases kept | |
| POST | `/organisations/{org_id}/close` | Close once no open cases remain | Owner, 🔒 |
| GET / PUT | `/organisations/{org_id}/billing-profile`, `/me/billing-profile` | Invoice issuer details | 🔒 for tax number |
| PUT, GET / PUT | `/me/billing-profile/bank-details`, `/organisations/{org_id}/billing-profile/bank-details` | Bank details for the invoice's "How to pay" box (organisation: owner or admin only) | 🔒 |
| POST | `/cases/{case_id}/named-expert` | Change the named expert (before serving only) | Owner/admin, 🔒; 409 after serving |
| PUT | `/me/custodian` | Name a records custodian | 🔒 |
| POST | `/me/close` | Close account after export; served cases kept read-only | 🔒 |
| POST | `/cases/{case_id}/support-access` | Named expert grants support 24-hour access to one case | 🔒; shown on the case |
| DELETE | `/cases/{case_id}/support-access/{grant_id}` | Revoke | |

**Case setup (slice C)**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET / POST | `/cases/{case_id}/members` | People on the case and their case role | Named expert or org admin |
| DELETE | `/cases/{case_id}/members/{member_id}` | Remove (history kept) | |
| GET / POST | `/cases/{case_id}/parties` | Parties to the claim, role keys from the pack | |
| PATCH / DELETE | `/cases/{case_id}/parties/{party_id}` | Edit or remove before serving | |
| GET / POST | `/cases/{case_id}/instructing-parties` | Instructing parties (two for a joint expert) | |
| PATCH | `/cases/{case_id}/instructing-parties/{ip_id}` | Change of firm (effective date) | |
| GET / POST | `/cases/{case_id}/instructions` | Instruction versions: letters and oral instructions | New version after lock reopens the question set |
| GET / POST | `/cases/{case_id}/links` | Linked cases | |
| DELETE | `/cases/{case_id}/links/{link_id}` | Remove a link | |
| POST | `/cases/{case_id}/conflicts/declare` | Declare a conflict mid-case; drafts the note | |
| POST | `/cases/{case_id}/withdraw` | Withdraw or record discharge `{reason}` | 🔒 |
| POST | `/cases/{case_id}/hold`, `/cases/{case_id}/resume` | Put on hold / resume | |
| PATCH | `/cases/{case_id}/jurisdiction` | Change jurisdiction before serving; packs re-stamped | 🔒; 409 after serving |
| POST | `/cases/duplicate-check` | Keyed-hash check of subject identifier + instructing party | Never stores the identifier |

**Records (slices A and B)**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/pages/{page_id}/quarantine` | "Not this person's record" `{reason}` | A; audited; drafts the note |
| DELETE | `/cases/{case_id}/pages/{page_id}/quarantine` | Undo, until the image is deleted | A |
| POST | `/cases/{case_id}/pages/{page_id}/replace` | Upload a clearer copy (new number; old one superseded) | A |
| POST | `/cases/{case_id}/pages/{page_id}/rotate` | `{degrees: 90\|180\|270}` clockwise; re-read through `process_pages`; number kept | B; 202 + job |
| POST | `/cases/{case_id}/facts/{fact_id}/move` | `{to_page_no, word_ids}` move a checked fact to the clearer copy | B; expert only |
| GET / POST | `/cases/{case_id}/exhibits` | Audio, video, imaging: listed, never paged | A |
| GET | `/cases/{case_id}/bundle/versions` | What each delivery of records added | A |
| POST | `/cases/{case_id}/process/cancel` | Stop processing at a page boundary | A |
| POST | `/cases/{case_id}/reprocess` | Opt in to a newer model; checked facts never change | A |
| GET / POST | `/cases/{case_id}/comments` | Comments on a page, fact or paragraph | D; never in reports |
| PATCH | `/cases/{case_id}/comments/{comment_id}` | Edit or resolve | D |

**Writing and reports (slices E and F)**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET | `/cases/{case_id}/opinions/{opinion_id}/versions` | Saved versions | E |
| POST | `/cases/{case_id}/opinions/{opinion_id}/versions/{version_no}/restore` | Restore as a new version | E |
| GET | `/cases/{case_id}/report/versions/{v}/snapshots` | What was frozen at signing | F |
| GET / POST | `/cases/{case_id}/report/versions/{v}/servings` | Record each recipient, date and method | F, 🔒 |
| POST | `/cases/{case_id}/report/versions/{v}/withdraw` | Withdraw a served report `{reason}` (stays locked) | F, 🔒 |
| PATCH | `/cases/{case_id}/invoices/{invoice_id}/payment` | Unpaid, part paid, paid, written off | F |
| POST | `/cases/{case_id}/invoices/{invoice_id}/credit-notes` | Correct a sent invoice | F, 🔒 |

**Court (slices G and I)**

| Method | Path | Does | Notes |
|---|---|---|---|
| GET / POST | `/cases/{case_id}/court-directions` | Record an order; creates or moves deadlines | G |
| GET | `/cases/{case_id}/deadlines/{deadline_id}/changes` | Deadline history | G |
| GET / POST | `/cases/{case_id}/hearings` | Hearings | G |
| PATCH | `/cases/{case_id}/hearings/{hearing_id}` | Adjourn (new date), vacate, mark heard | G; marks packs stale |
| GET / POST | `/cases/{case_id}/written-question-rounds` | Batches of written questions with deadlines | G |
| GET / POST | `/cases/{case_id}/expert-meetings/{meeting_id}/participants` | Experts in the meeting | I |
| GET / POST | `/cases/{case_id}/expert-meetings/{meeting_id}/agenda-versions` | New agenda version; differences shown | I |
| POST | `/cases/{case_id}/expert-meetings/{meeting_id}/signed-external` | Upload a document signed outside Avodeck | I |
| POST | `/cases/{case_id}/court-bundles/{bundle_id}/supersede` | Re-issued bundle replaces the old one; new concordance | I |

**Close (slice H)**

| Method | Path | Does | Notes |
|---|---|---|---|
| POST | `/cases/{case_id}/legal-holds` | Stop deletion `{reason, until}` | Named expert or org owner, 🔒 |
| POST | `/cases/{case_id}/legal-holds/{hold_id}/release` | Lift the hold; retention resumes | 🔒 |
| GET | `/cases/{case_id}/audit` | The case's audit trail for the expert, including any support access | |

---

## 6. AI

### 6.1 Model roster

| ID | Model | Runs where | Used for | Mode |
|---|---|---|---|---|
| **M1** | Azure AI Document Intelligence `prebuilt-layout` | Azure UK South (API) | OCR of records, letters of instruction, witness statements, written questions, agendas: words, word polygons, confidence, handwriting flag, tables | Batch (worker) |
| **M2** | Azure AI Document Intelligence `prebuilt-read` | Azure UK South (API) | Cheaper text-only OCR for court bundles and the other expert's report | Batch |
| **M3** | Claude Sonnet 5, Bedrock **in-region** model ID | AWS London (API) | Workhorse: per-page extraction, question parsing, classification, linking, linting, comparison | Batch for pipeline; real-time for linting and screens |
| **M4** | Claude Opus 5.5, Bedrock **in-region** model ID | AWS London (API) | Hard two-document judgements only: opposing-report verification, conflicting accounts, joint-statement deviation | Real-time, low volume |
| **M5** | Claude Sonnet 5 **vision** (same endpoint as M3) | AWS London (API) | Second reading of low-confidence handwriting pages, identifier areas masked; shown only after the expert has transcribed | Batch |
| **M6** | Azure Speech real-time, language from the jurisdiction pack (en-GB at launch) + Custom Speech phrase list from the overlay pack (local drug names, abbreviations) | Azure UK South (API) | All dictation | Real-time streaming |
| **M7** | `bge-small-en-v1.5` embeddings (33M params, CPU) + pgvector | FastAPI worker, DigitalOcean London | Finding candidate links: events to questions, facts to drafts, notes to served documents | Batch + real-time |
| **M8** | `bge-reranker-base` cross-encoder (CPU) | FastAPI worker, DigitalOcean London | Re-ordering M7 candidates before M3 sees them | Real-time |
| **M9** | Microsoft Presidio + spaCy model for the pack's language + recognisers from the jurisdiction pack (at launch: NHS number mod-11, UK postcode) + DOB and per-case name list | FastAPI worker, DigitalOcean London | Stripping identifiers before every M3/M4/M5 call | Inline |

Later, once confirmed as London in-region: Claude Haiku 5.5 can replace M3 for simple classification (T-13a, T-15a regex fallbacks) to cut cost.

**Not models (deterministic code):** perceptual hashing (`imagehash`), MinHash/LSH (`datasketch`), fuzzy matching (`rapidfuzz`), sequence alignment for trial pages, date normalisation (`dateparser`, date order from the jurisdiction pack), quote verification, all final checks except tone and expertise.

### 6.2 Rules every model call follows

1. **Identifiers out:** M9 runs first; names, the jurisdiction's identifier numbers, addresses and DOBs are replaced with tokens. Dates of events are kept.
2. **Structured output only:** Claude is called with a JSON schema (tool use), temperature 0. Free text is never accepted where a schema exists.
3. **Provenance enforced in code:** every extracted item must list OCR `word_ids`. The server rejects any value that does not appear in those words (after normalising case, spacing and number format). One retry, then the item is marked "needs human".
4. **Claude labels, never invents:** where facts already exist (screens 14, 15, 22), Claude receives verified fact IDs and may only sort or link them.
5. **No opinion generation:** no prompt asks for an opinion, a conclusion or rewritten prose.
6. **Caching and batch:** a stable system prompt + schema sits at the head of each call (cached); pipeline calls go through batch where offered in-region.
7. **Logged for the methodology appendix:** model ID, region, prompt version and date for every task, never the content.
8. **Logging off:** Bedrock invocation logging off; Azure data logging off; no prompts or outputs in app logs.
9. **Prompts and schemas come from packs:** prompt templates are neutral and contain no country, specialty or case-type words. `pack.prompt_context()` injects, as data, the fact kinds and their field schemas, source types, record-system cues, vocabulary and date order. Output JSON schemas (e.g. allowed `kind` values) are generated from the merged pack registries, so a new specialty needs no prompt rewrite. Each prompt version is evaluated per pack combination in `evals/`.

### 6.3 AI tasks in journey order

| Screen | Task ID | Model | Trigger | Input | Output (schema fields) | Check before it's used |
|---|---|---|---|---|---|---|
| 01 Sign in | — | No AI | — | — | — | — |
| 02 Profile | T-02 Template mapping (optional) | M3 | Template uploaded | List of headings and styles from the .docx | `{heading, maps_to_section: intro|instructions|documents|chronology|opinion|range|summary|declarations|other}` | Expert confirms the mapping |
| 03 Today | — | No AI | — | — | — | — |
| 04 New case | T-DICT Dictation | M6 | Mic pressed | Audio stream | Transcript text + word timings | Expert edits; drugs and numbers highlighted; audio deleted |
| 04 New case | T-04 Prefill from letter (optional) | M1 → M3 | Letter uploaded | LOI OCR text | `{instructing_party_name, instructing_party_type, party_role, due_date, court_track?, word_ids per field}` (allowed values from the packs) | Expert confirms each field |
| 05 Conflicts | — | No AI | — | — | — | — |
| 06 Questions | T-06 Question and assumption extraction | M1 → M3 | LOI OCR done | LOI OCR text with word IDs | `questions[{number, text_verbatim, word_ids, para_ref, type: (from the case-type pack's question types), outside_expertise: bool, reason}]`, `assumptions[{text_verbatim, word_ids}]` | Exact-substring check; expert confirms each |
| 07 Preparing | T-07a OCR | M1 | Each page | Page image | Words, polygons, confidence, handwriting flag, tables | Low confidence → screen 10 |
| 07 Preparing | T-07b Per-page extraction | M3 (batch) | After OCR + M9 | One page's words with IDs + previous page header | `source_type`, `printed_dates[]`, `facts[{kind: (generated from the pack's fact-kind registry; at launch consultation, phone_consultation, home_visit, result, prescription, referral, letter, examination, safety_netting, missed_appointment, task), clinician_role, fields (schema per kind from the pack), value, word_ids}]`, `references_out[{described_document, date, word_ids}]`, `availability_evidence[{kind: received|filed|seen|sent_to_clinician, date, word_ids}]`, `actions_requested_of_clinician[{text, word_ids}]` | Value-in-span check; dates normalised by code; nothing shown until the page is read |
| 07 Preparing | T-07c Missing documents | M7 + M3 | All pages extracted | `references_out` list + document inventory | `{reference_id, matched_page | null, confidence}` | Unmatched → missing list; expert confirms |
| 07 Preparing | Duplicates, gaps, ordering | No AI | — | — | — | Expert reviews duplicates |
| 08 / 08b | — | No AI | — | — | Template email | Expert edits before sending |
| 09 Reader | — | No live AI | — | Shows T-07b results after the page is marked read | — | Correct / Not right / Change a detail |
| 10 Couldn't read | T-10 Second reading | M5 | After expert transcribes, or expert asks | Masked page image | `{lines[{text, confidence}]}` shown as a comparison | Expert's text is authoritative; then T-07b reruns on it |
| 10 Couldn't read | T-DICT | M6 | Mic | Audio | Transcript | Expert edits |
| 11 Timeline | T-11 Link events to questions | M7 → M8 → M3 | After extraction; on new verified facts | Questions + candidate facts | `{fact_id, question_id, relevant: bool, reason}` | Shown as suggested links; expert confirms |
| 12 As the clinician saw it | — | No new AI | Uses `availability_evidence` from T-07b | — | Code decides: visible only if available on or before the consultation date | Unknown shown as unknown; expert can override with reason |
| 12 As the clinician saw it | Guidance in force | No AI | — | Curated guideline table (sources from the overlay pack) | — | Expert confirms |
| 13 Things to look at | T-13a Does this text count as action / safety-netting? | M3 | Rule finds a candidate | The specific spans + the rule | `{label: action_taken|no_action|safety_netting|none, word_ids}` | Expert marks Relevant / Not relevant / Explained elsewhere |
| 13 Things to look at | T-13b Custom check builder (later) | M3 | Expert describes a check | Plain-English description | Rule JSON (event types, time window, condition) | Previewed on this case before enabling |
| 14 Points against | T-14a Sort facts for and against | M3 | Question opened | Question + verified fact IDs with text | `{fact_id, side: supports_claim|supports_clinician|neutral, reason}` | Can only use supplied IDs; expert moves and accepts |
| 14 Points against | T-14b Relevant facts not yet used | M7 → M8 → M3 | Draft saved | Draft text + unused verified facts | `{fact_id, why_relevant}` | Expert dismisses or uses |
| 14 Points against | T-14c Conflicting accounts | M1 → M4 | Witness statement uploaded | Statement text + retrieved verified facts | `claims[{text_verbatim, word_ids}]`, `verdicts[{claim_index, fact_ids, verdict: supports|contradicts|not_addressed}]` | Both quotes shown side by side; expert confirms |
| 15 Opinion | T-DICT | M6 | Mic | Audio | Transcript | Expert edits |
| 15 Opinion | T-15a Lint | Regex first, then M3 | Pause or save | Paragraph + its citation objects + expert's specialty | `issues[{sentence_index, type: uncited_fact|loaded_language|outside_expertise, words}]` | Warnings only; never rewrites; dismiss with reason |
| 16 Report | — | No AI | — | — | — | — |
| 17 Final check | T-17 Tone and expertise pass | M3 (T-15a over the whole report) | Final check run | Whole report | Same schema as T-15a | All other 26 checks are code |
| 18 / 18b / 19 | — | No AI | — | — | — | — |
| 20 Written questions | T-20a Extract questions | M1 → M3 | Upload | Questions document | Same schema as T-06 | Exact-substring check; expert confirms |
| 20 Written questions | T-20b Classify and link | M7 → M3 | After extraction | Question + served-report paragraphs | `{question_id, classification: (from the jurisdiction pack, e.g. clarifies|goes_beyond), paragraph_ids[], reason}` | Expert decides (legal judgement) |
| 20 Written questions | T-DICT | M6 | Mic | Audio | Transcript | Expert edits |
| 21 New material | New pages | T-07a/b/c | Upload | New pages | As screen 07 | Blind read applies |
| 21 New material | T-21a May matter | M7 → M3 | New pages extracted | New facts + questions | `{fact_id, question_id, why}` | Expert reads every page anyway |
| 21 New material | T-21b Other expert's claims | M2 → M3 | Report uploaded | Report text | `claims[{text_verbatim, word_ids, cited_page, cited_value}]` | Expert reviews |
| 21 New material | T-21c Verify claims | Code, then M4 | After T-21b | Claim + text of cited page (+ nearby pages from M7) | `{status: matches|wrong_page|wrong_value|not_on_page|contradicted, evidence_word_ids}` | Code checks page and value first; M4 only for the rest; both quotes shown |
| 21 New material | T-21d Compare positions | M3 | After T-21b | Expert's paragraphs + other expert's paragraphs per question | `{question_id, own_paragraph_ids, their_paragraph_ids}` | Verbatim side by side, no summary |
| 21b Corrections | T-21e Dependent paragraphs | Code, then M3 | Paragraph changed | Changed paragraph + paragraphs sharing citations | `{paragraph_id, depends: bool, reason}` | Expert reviews each |
| 21b Corrections | T-DICT | M6 | Mic | Audio | Reason for change | Expert's words only |
| 22 Expert meeting | T-22a Agenda items | M1 → M3 | Agenda uploaded | Agenda text | Same schema as T-06 | Expert confirms |
| 22 Expert meeting | T-22b Prefill from served report | M7 → M3 | After T-22a | Agenda item + served paragraphs | `{agenda_id, paragraph_ids[]}` (verbatim text inserted by code) | Expert edits |
| 22 Expert meeting | T-22c Deviation check | M4 | Position saved | Draft position + linked served paragraphs | `{status: consistent|changed|unclear, reason}` | "Changed position" warning; expert records the change |
| 23 Court bundle page numbers | T-23 OCR | M2 | Court bundle uploaded | Page images | Words only | — |
| 23 Court bundle page numbers | Matching | No AI | — | — | Concordance | Low scores reviewed by expert |
| 24 Trial pack | T-24 Nothing new on the day | M7 → M3 | Pack built | Each note sentence + candidate served passages | `{sentence_index, source: {document, paragraph_id} | null}` | Unsourced notes flagged; expert decides |
| 25 Witness box | — | No AI (offline; cannot call APIs) | — | — | — | — |
| 26 Close | — | No AI | — | — | — | — |

### 6.4 Volume per case

| Model | Calls per case | Main tasks |
|---|---|---|
| M1 Layout OCR | ~1,270 pages + LOI and other documents | T-07a, T-06, T-14c, T-20a, T-22a |
| M2 Read OCR | ~4,000 pages (court bundle) + other expert's report | T-21b, T-23 |
| M3 Sonnet 5 | ~1,300 (one per page) + ~100–300 smaller calls | T-07b and everything else |
| M4 Opus 5.5 | ~10–60 | T-14c, T-21c, T-22c |
| M5 Vision | ~30–130 pages (low-confidence handwriting only) | T-10 |
| M6 Speech | 3–5 hours of audio | T-DICT |
| M7 / M8 | Thousands, on your own CPU at no API cost | Candidate search |
| M9 Presidio | Before every M3/M4/M5 call, on your own CPU | Identifier stripping |

### 6.5 What to measure

- **T-07b extraction:** precision = verified ÷ (verified + not right); recall, measured with a hand-labelled synthetic set; rate of value-in-span rejections.
- **T-07a / T-10 handwriting:** share of pages sent to screen 10; share where the expert's text differs from M5's.
- **T-06 / T-20a / T-22a:** questions confirmed unchanged ÷ total.
- **T-11 / T-14 / T-21a:** suggested links accepted ÷ suggested.
- **T-15a lint:** warnings dismissed ÷ raised (too many dismissals = too noisy).
- **T-21c / T-22c:** discrepancies the expert confirmed ÷ flagged.
- **T-DICT:** edits per 100 words; drug and number corrections counted separately.

---

---

## 7. Bundle pipeline (worker)

**Batch pipeline per case:**
1. **Ingest:** Supabase Storage upload → ClamAV → pikepdf/qpdf split and repair → pypdfium2 render (150–200 dpi) → hashes (FastAPI worker on DigitalOcean App Platform). Never PyMuPDF (AGPL).
2. **Text layer or OCR:** for each page, check for a usable embedded text layer (born-digital hospital letters, discharge summaries, results). If present, read words and their boxes directly with pypdfium2, give them `word_ids` in `ocr_words` like any OCR output, and skip Azure; set `pages.text_source = 'embedded'`. Treat the layer as unusable (send to OCR) if it is empty, mostly non-dictionary characters, or covers only part of a page that also contains an image of text (scans with a stamped header). Everything else goes to Azure DI Layout (UK South), bounded concurrency, `text_source = 'ocr'`. Embedded-text pages get confidence 1.0 and are never sent to screen 10 for unreadable text. Docling and other local layout models are not used (see decision 0009).
3. **Triage:** confidence + OCR/VLM disagreement → screen 10; Claude vision fallback with identifier boxes masked.
4. **PII stripping:** Presidio + case dictionary (dates kept).
5. **Classification + event extraction:** one Sonnet 5 call per page with cached system prompt + JSON schema, returning source type, clinical and availability dates, and facts with OCR word IDs.
6. **Deterministic verification:** value-in-span, date normalisation in the pack's date order, schema validation; one retry, then "needs human".
7. **Case-level passes:** dedup, missing references, gaps, anomaly rules, availability computation.
8. **Indexing:** Supabase Postgres full-text + pgvector; timeline view.
9. **Notify:** Resend email (no patient data) + in-app update via server-sent events from FastAPI.

**Real-time vs batch:** real-time for dictation, linting, fact chips, filters, report assembly and final checks; batch for OCR, extraction, duplicates, checks, court-bundle matching and opposing-report verification. Use Bedrock batch where the in-region model supports it, otherwise on-demand with prompt caching (stable system prompt + schema at the head of each call).

---

## 8. How we build

### 8.1 Principles

- Vertical slices, each owning its screens, endpoints, tables, AI tasks and tests end to end.
- A short shared foundation first; slices then run in parallel.
- Contract first: schemas, stub endpoints, migration and regenerated client before code.
- Test-first (red → green → refactor); tests are frozen during implementation (hook-enforced, CI-checked).
- Small, always-deployable merges behind feature flags.
- Synthetic bundles only until the compliance basics are done (DPIA, ICO registration, provider DPAs, expert processor agreement, Cyber Essentials).
- Every slice is shown to 2–3 design-partner experts as it lands.

### 8.2 Test layers

| Layer | Tool | Written first from | Example |
|---|---|---|---|
| Acceptance (per screen) | Playwright | Screen spec + canvas | Opening a page shows no suggestions until it's marked read |
| API contract | pytest + httpx; Schemathesis | Section 5 endpoints | `GET /pages/118/facts` returns 423 before `/read` |
| Database and security | pytest against local Supabase; pgTAP for RLS | Data model | Expert B gets 404 on expert A's case |
| Service and domain | pytest | Root rules | A fact whose value isn't in its cited words is rejected |
| AI tasks | pytest with recorded responses; separate nightly live evaluation | Section 6 tasks | T-06 questions exactly match the letter's text |
| Frontend components | Vitest + Testing Library | Design system + glossary | "Please check" badge uses the dashed border and plain wording |
| Accessibility | axe in Playwright | Design brief | WCAG AA; buttons at least 52px |

**Non-negotiable tests, written in the foundation week:**
- Blind read: facts return 423 until the page is read.
- Ownership: another expert's case returns 404 on every case route.
- Provenance: a value not in its cited words is rejected; only verified facts can be cited.
- Locked report: editing a served version returns 409; changes go through the supplementary report flow.
- Step-up: 🔒 endpoints refuse a session without recent MFA.
- Server-side only: the frontend build contains no Supabase, Azure or AWS hosts; the CSP allows only the API.
- Home region only: Claude calls use only the cell's in-region model IDs (UK: London).
- No patient text in logs: a made-up identifier (from each pack's recogniser) and name never appear in log output.
- Pack-driven: core flows give different words and rules under the fixture packs (3.6).
- No generated opinion: AI outputs contain no free-prose fields.

**AI testing:**
- **Normal tests:** Bedrock, Azure OCR and Azure Speech are replaced with recorded responses from the synthetic bundle, so tests are fast, free and identical on every run.
- **Nightly evaluation:** the real models run against a hand-labelled synthetic bundle. It fails if extraction precision falls below threshold or the share of pages sent for manual reading rises.

### 8.3 Foundation

- **Platform:**
  - Login through FastAPI with the cookie session, MFA (with recovery codes), CSRF and step-up; device sessions.
  - The `people`, `experts`, `organisations` (personal ones only at launch), `memberships`, `cases`, `case_members`, `documents`, `pages`, `jobs`, `notifications`, `notification_preferences`, `feature_flags`, `audit_log` and `idempotency_keys` tables, with RLS.
  - `require_case_access(action)` and the permissions matrix (`docs/spec/permissions.md`) as one tested table.
  - ETag/If-Match and `Idempotency-Key` middleware; scheduled jobs keyed by date; per-case job lock.
- **Files:** chunked uploads, file streaming, and the job queue with server-sent progress events.
- **AI kit:**
  - Presidio identifier stripping.
  - Bedrock client pinned to London in-region models.
  - Azure OCR client.
  - Provenance validator.
  - `ai_calls` logging.
  - Recorded-response fixtures.
- **Dictation kit:** WebSocket relay to Azure Speech, plus the reusable dictation box.
- **Frontend shell:**
  - Design-system components from the canvas.
  - Case header with step pills, and the glossary.
  - Typed API client from OpenAPI.
- **Core contracts, defined now and filled by other slices:**
  - the `facts` table and its statuses;
  - the `questions` table;
  - the `report_versions` table;
  - the "needs you" interface each slice registers with Today (03).
- **Country-neutral seams (section 3):** `jurisdiction` on cases, the `JurisdictionPack` interface and the `gb-eaw` pack, i18n keys for every string, region config (`REGION=uk`) with the cell allow-list.
- **Operations:** CI, staging and production deploys, and the non-negotiable tests.
- **Engine seams (section 3):** `case_type`, `specialty` and `expert_role` on cases; the pack loader with all four layers (only `gb-eaw`, `clin-neg-breach` and `general-practice` filled in); registries for fact kinds, source types, question types, lanes, checks and report sections; workflow steps from the case-type pack; organisations and memberships (personal organisations at launch; teams in slice O); "clinician", "subject" and "instructing party" in code.

### 8.4 Slices

| Slice | Screens | Owns (backend) | AI tasks | Needs | Gives to others | Phase |
|---|---|---|---|---|---|---|
| **A. Bundle pipeline** | 07, 08, 08b | Worker pipeline (split, OCR, extraction, dates, duplicates, gaps, missing items); `ocr_words`, `facts` (writes), `missing_items`, `record_gaps`, `instructing_party_requests`; append-records route | T-07a/b/c | Foundation | Facts with word references, page images; reused by G for new pages | MVP |
| **B. Reader** | 09, 10 | Page endpoints with blind-read gating (423), `page_reads` log, `fact_verifications`, transcription and re-extraction | T-10 | Facts (stub data until A lands) | **Verified facts**, cited by every later slice | MVP |
| **C. Case setup** | 02, 04, 05, 06 (+ Today cards) | Profile, roles, template parsing, signature; case creation and uploads; conflicts; question and assumption extraction | T-02, T-04, T-06 | Foundation | Confirmed questions, expert profile and template | MVP |
| **D. Understand** | 11, 12, 13 | Timeline, fact–question links, availability computation, curated guideline table, checks engine (rule types in code, rule instances in packs) and findings | T-11, T-13a | Verified facts (B), questions (C) | Links and findings for E and F | MVP (12 basic) |
| **E. Write** | 14, 15 | Points for and against, range notes, witness-statement comparison, opinion workspace, citation objects, linting | T-14a/b/c, T-15a | Verified facts, questions, links | Opinion text and citations for F | MVP |
| **F. Report and sign** | 16, 17, 18, 18b | Word assembly in the expert's template, compliance checklist from the pack, methodology appendix, 28 final checks, signing, locking and hashing, serving, time tracking, invoices | T-17 | Opinions (E), template (C), page-read log (B) | **Locked served versions** for G, I and H | MVP |
| **G. After serving** | 19, 20, 21, 21b | Deadlines (pack calendars and timezones), written questions, new records (reusing A), other expert's report verification, corrections and supplementary report | T-20a/b, T-21a–e | Served version (F), pipeline (A) | Updated positions for I | Phase 2 |
| **H. Close and compliance** | 26 | Export ZIP, retention, scheduled deletion with crypto-shredding, deletion certificate, archive and reopen | None | Foundation | Proof of deletion; DPIA evidence | MVP |
| **O. Organisations and accounts** | Organisation settings, members and invites, account and security, notification preferences | Team organisations, invites, role changes, leaving, reassignment, organisation and account closure, custodian, billing profiles, support access grants | None | Foundation | Teams for agencies, chambers and NHS teams; the same cases now shared by role | Phase 2 |
| **I. Trial** | 22, 23, 24, 25 | Expert meetings and e-signature webhook, court-bundle OCR and page matching, trial pack PDF, offline package | T-22a/b/c, T-23, T-24 | Served version (F), positions (G) | — | Phase 3 |

**Today (03)** belongs to the foundation. Each slice registers its own "needs you" items, e.g. A adds "bundle ready", B adds "pages left to read", G adds "written questions due" (labels from the pack).

**Later slice (not in the MVP):** **J. Examination and prognosis**: subject's account and examination findings (structured, dictated), injury list with the expert's prognosis, before/after-accident view on the timeline, condition-and-prognosis report sections. Needed for `clin-neg-causation`, `pi-condition-prognosis` and similar packs.

### 8.5 Running slices in parallel

```
Order (not weeks):
You:      Foundation stage 1 → stage 2 → review/merge ─────────────────▶
Lane 1:                                  A Pipeline  →  B Reader  →  D Understand
Lane 2:                                  C Setup     →  E Write   →  F Report & sign
Lane 3:                                  H Close     →  (help F: final checks) → G, O (Phase 2)
```

**Realistic effort (part-time, one reviewer):** foundation 4–6 weeks (stage 1 about 3–4, stage 2 about 1–2); each MVP slice 2–4 weeks; MVP about 4–6 months. Re-estimate after the foundation, using how long its pieces actually took.

- **Three lanes at once,** the most one reviewer can handle. Each slice runs as its own Claude Code session in its own git worktree and branch.
- **Stubs keep lanes moving:** B can start on stub facts before A finishes, and E on stub verified facts, because the contracts were fixed in the foundation.
- **After the MVP:** G, then I.

**Rules that keep the split clean:**
- Each slice writes only its own tables. It reads others' data through their endpoints or the shared contracts.
- Migration number ranges: foundation 0000–0099, A 0100–0199, B 0200–0299, C 0300–0399, D 0400–0499, E 0500–0599, F 0600–0699, G 0700–0799, H 0800–0899, I 0900–0999, O 1000–1099.
- Changes to the foundation, design system or a contract are requested by the slice and approved by you.
- Contracts only grow: endpoints can gain fields, never break.

### 8.6 Order and phases

1. Line up 2–3 design-partner experts; usability test the canvas prototype with them.
2. Foundation stage 1 (local), test-first. Alongside it, set up the cloud accounts and run spikes 1–4 and 8 (`docs/spikes/`); update decisions if any fail.
3. Foundation stage 2 (cloud services, deploys).
4. Slices A + C + H in parallel, then B + D, then E + F. That completes the MVP.
5. Alongside the build: the compliance basics before any real data.
6. Pilot with one expert on one consented case.
7. Paid design partners.
8. Phase 2 (slices G and O), then Phase 3 (slice I), in the order real usage suggests.

At every gate (before the MVP, each phase, each new country or specialty) run the phase-gate re-audit in 10.14; `scripts/check_spec.py` must report no gaps.

**Also sort early:** professional indemnity and cyber insurance; terms stating the expert is responsible for their opinion; a short incident-response plan.

---

**Expansion order after launch** (each only when the previous one has paying users and a stable accuracy score):
1. Specialty packs for clinical negligence breach: emergency medicine, orthopaedics, obstetrics.
2. Causation and condition-and-prognosis in clinical negligence (needs slice J).
3. Personal injury condition and prognosis (non-whiplash).
4. Another country (Australia first), as a new cell plus a jurisdiction pack. The likely first Australian market is IME work for compensation schemes, piloted in Victoria (WorkSafe Victoria and TAC), which needs slice J and a base `ime` case-type pack. NSW is unsettled by its 2025 reforms until 2027. See `docs/research/background.md`.

---

## 9. Security and compliance requirements that change the code

- **Login:** MFA mandatory; session in an httpOnly, Secure, SameSite=Strict cookie; CSRF token on state changes; 30-minute inactivity timeout; sign-ins logged.
- **Cloud credentials:** App Platform can't assume AWS IAM roles, so each service (api, worker, deletion job) has its own narrowly scoped IAM user: Bedrock invoke on the named London model ARNs only, KMS on the cell key only, every request restricted to eu-west-2 by policy condition. Keys live in App Platform encrypted environment variables, are rotated every 90 days, and CloudTrail alerts on any use outside eu-west-2 or any new key creation. Moving to IAM Roles Anywhere (short-lived certificate-based credentials) is in `docs/decisions/deferred.md`.
- **Data:** encryption at rest (Supabase) plus per-case envelope keys for page text and images, wrapped by AWS KMS London (ADR 0010), so deletion can crypto-shred. Slices call `core/crypto.py` only.
- **Status changes:** only those in `docs/spec/state-machines.md`, enforced in services and by database triggers.
- **Threats and failures:** `docs/security/threat-model.md` and `docs/spec/failure-modes.md`; each row maps to a test.
- **Logs:** append-only, hash-chained audit, page-read and verification logs.
- **AI:** Bedrock invocation logging off; Azure data logging off; no prompts or outputs stored anywhere except `ai_calls` metadata.
- **Deletion:** retention per case (pack default), scheduled deletion across every registered case table and storage path, with a certificate stating the backup expiry date.
- **Sub-processors:** each provider listed with region; anything outside the cell carries no patient data (Resend, Stripe, Sentry, PostHog, Vercel).
- **External sharing:** colleague spot-checks share watermarked page crops only, via expiring links with email codes.

---

## 10. Behaviour rules

Section 4 says what is stored; this section says how it behaves: who can do what, what happens when there are several of something, when things change, and when people leave. Every rule here has a test in the owning slice. The permissions matrix is in `docs/spec/permissions.md`, end-to-end scenarios in `docs/spec/scenarios.md`, and one line per table in `docs/spec/behaviour-index.md`. Decisions with legal weight are recorded in ADR 0012 and marked **(legal check)** until a lawyer confirms them.

### 10.1 People, organisations and access

- **One identity per person per cell.** One email, one password, one MFA. The global directory maps an email to its cell(s); a person registered in two cells chooses the country at sign-in. Case data never crosses cells.
- **Organisations.** Every person has a personal organisation (created at sign-up, cannot be deleted while it holds cases). Teams (agencies, chambers, NHS teams) are further organisations. Each case belongs to exactly one organisation, chosen when the case is created ("Who is this case for?", default the personal organisation).
- **Roles in an organisation:** owner, admin, expert, assistant, viewer (`docs/spec/permissions.md`). Assistants and viewers see only the cases they are assigned to; admins and owners see all the organisation's cases. Only the named expert signs, verifies facts, writes opinions or serves.
- **Named expert.** `cases.named_expert_id` is the person whose evidence it is. Reading (`page_reads`), fact verification and opinions are personal: only the named expert's reads and verifications make facts citable in their report. Other people on a case (supervisor, assistant, co-expert) can view and comment, never verify or sign.
- **Case members.** `case_members` adds a specific person to a case with a case role (assistant, supervisor, co-expert, viewer), for example an agency assistant or a supervising consultant.
- **Access rule (replaces "belongs to the signed-in expert"):** a person may act on a case if they are its named expert, or a case member whose case role allows the action, or an owner/admin of its organisation for administrative actions only (not reading patient content unless also a case member). `require_case_access(action)` in `core/auth/` is the only check; RLS mirrors it.
- **Unified dashboard.** Today lists every case and deadline from all the person's organisations, with an organisation badge and a filter. Notifications follow per-organisation preferences.
- **What follows the person, not the organisation:** professional identity, registration, CV and training, signature, declaration, conflict checks (across all their cases in all organisations) and balance figures (across all their work).
- **What can sit on either:** report template, letterhead, default retention, billing details. Organisation policy can require its template and set retention limits; otherwise the person's defaults apply.
- **Invites.** By email, role chosen by an owner or admin (step-up), expire after 7 days, single use. Accepting creates a membership.
- **Leaving an organisation.** The membership ends (`ended_at`). Access to that organisation's cases ends, **except** that the named expert keeps access to every case on which they are named, including all court-stage actions, for as long as the case exists. The organisation keeps its administrative copy. Both receive deletion certificates. **(legal check)**
- **Reassignment.** Before a report is served, an owner or admin may change the named expert, with the outgoing expert told. The new expert starts their own reading and verification from zero (reads and verifications are personal). After serving, a case cannot be reassigned: a replacement expert gets a new linked case (`case_links.kind = replaces`), with the bundle copied only with the instructing party's consent recorded.
- **Organisation closure.** Only an owner can close an organisation, after every case is exported, deleted or (unserved) transferred. Served cases stay until retention ends, and named experts keep access.
- **Account closure.** A person can close their account after exporting. Unserved personal cases are deleted (step-up). Served cases are locked and kept until retention, because the expert may still be called to court; the account becomes sign-in-only for those cases. **(legal check)**
- **Death or incapacity.** A person can name a records custodian in their profile. With evidence, support can transfer custody of served cases to that custodian or to the organisation, read-only, for the retention period. **(legal check)**
- **Controller and processor.** For personal-organisation cases the expert is the data controller; for team cases the organisation is; Avodeck is the processor in both, under a data processing agreement accepted at sign-up and on organisation creation. **(legal check)**
- **Support access.** Avodeck staff cannot see case content. Support works from metadata. Break-glass access needs the named expert's time-limited consent (24 hours, one case), is logged in the audit trail and shown to the expert.
- **MFA recovery.** Ten one-time recovery codes at enrolment. Without codes: identity re-verification, a 24-hour delay and notice to the account email; organisation admins cannot bypass it.
- **Email change.** Confirm from both the old and new address; step-up required.
- **Audit.** Every audit row records the person, the organisation they acted for, the case, the action and the target.

### 10.2 Cases, parties and instructions

- **Parties.** `case_parties` holds any number of parties, each with a role key from the packs (e.g. instructing side, other side) and a name, for example a claim against both a GP and a hospital trust.
- **Instructing parties.** `case_instructing_parties` holds one or more (single joint experts are instructed by both sides). For a joint instruction, every communication drafted by Avodeck is addressed to all instructing parties, and serving records each recipient. Changes of firm are kept with effective dates; drafts go to the current firm.
- **Subject status.** Living, deceased or minor (with a litigation friend), keys from the packs. It changes vocabulary ("the deceased") and nothing else in code.
- **Instructions are versioned.** `instruction_versions`: the original letter, supplementary letters and dated oral instructions. Questions belong to an instruction version. A new question after the set is locked reopens the set (state machines); the report records which instruction versions it answers.
- **Conflicts mid-case.** The expert can declare a conflict at any time. Avodeck drafts the note to the instructing parties; the expert may continue (with disclosure) or withdraw.
- **Withdrawal and discharge.** Case status `withdrawn`, with reason (expert withdrew, discharged, permission refused, conflict). Served reports stay locked; retention starts.
- **Duplicate cases.** On creation Avodeck warns if one of the person's cases has the same subject identifier (compared as a keyed hash, never stored in clear) and instructing party.
- **Linked cases.** `case_links` (related claim, inquest, previous instruction, replaces). Links show on each case and never share data automatically.
- **Jurisdiction.** Set at creation. It can change only before serving; packs are re-stamped and the expert is warned what changed.
- **On hold.** A case can be put on hold (stay, awaiting records). Reminders pause; nothing is deleted.

### 10.3 Records

- **Wrong-patient pages.** The expert marks pages "not this person's record", or accepts a suggestion. The pipeline suggests when identifiers on a page (from the pack's recognisers) do not match the case subject. The page is then:
  - quarantined: hidden, excluded from AI, facts, search, citations and exports;
  - replaced in the reader by a placeholder that keeps its number ("Page removed: not this person's record, date, reason");
  - stripped of any facts, which are rejected with that reason (cited ones fail the final check);
  - scheduled for deletion of its image and text after the instructing party is told (drafted note).
  Every step is audited. MVP scope.
- **Clearer copies.** A replacement page gets the next number. The old page is marked superseded (`pages.superseded_by_page_id`) and stays readable. New suggestions are extracted from the new page. The expert can move a verified fact to the clearer copy in one click; this never happens automatically.
- **Rotation.** Rotating a page re-runs OCR and keeps its number. Verified facts on it stay; their boxes are re-mapped, or flagged "Please check" if the words moved.
- **Inputs other than PDF:**
  - images (JPEG, PNG, TIFF, HEIC) become PDF pages;
  - emails (.eml, .msg) are rendered as pages, with attachments as separate documents;
  - Word files are converted;
  - zip files are unpacked with a manifest.
  Audio, video and medical imaging files (DICOM) are stored as exhibits, listed in the bundle summary as "not readable by Avodeck", and never given page numbers.
- **Foreign-language pages** are detected and flagged; there is no machine translation. A draft request for a certified translation is offered.
- **Reprocessing.** A new OCR or AI model never reprocesses existing cases automatically. The expert may opt in per case; verified facts never change, and new suggestions are labelled with their model version.
- **No redaction of source records.** The bundle is evidence and is kept as received. The reader shows every page exactly as received, identifiers included (the expert needs them, e.g. to spot another patient's page). Identifiers are removed only from what leaves the expert's view: AI calls, logs, emails, and colleague spot-check crops.
- **Bundle versions.** Each batch of appended records forms a new bundle version (v1, v2…). The index and summary show what each version added.

### 10.4 Facts and citations

- **Stable identity.** A fact keeps its ID for life. Re-extraction adds new suggested facts and supersedes earlier **unverified** suggestions from the same page; verified facts are never superseded automatically.
- **Citations snapshot the fact.** A citation stores the fact's value and version at the time of citing. If the fact later changes or is rejected, the citation shows "changed since cited" and the final check fails until the expert reviews it.
- **Duplicates.** Facts on a page set aside as a duplicate appear only on the original.

### 10.5 Writing

- **Opinion history.** Every autosave keeps a version. Building a report names the versions used. Earlier versions can be viewed and restored, never edited.
- **Guidance table changes.** Guideline versions come from the overlay pack (ADR 0016); the table version is the overlay pack version and its `reviewed_at`. A case records the version it was shown; if a cited guideline's entry changes, the expert sees a notice.
- **Comments.** `comments` on a page, fact or paragraph, by case members. They never appear in reports. The screen warns that comments may be disclosable.
- **Concurrent editing.** Every editable resource uses optimistic concurrency (ETag / If-Match; 412 on a stale write) with a "which version do you want to keep?" prompt. This applies to all editable resources, not only opinions.

### 10.6 Reports

- **Snapshots at signing.** The signed version freezes copies of:
  - CV, declarations and signature image;
  - template and pack versions;
  - guideline table version, instruction versions and conflict answers.
  Later profile changes never alter it.
- **Numbering.** Drafts are numbered per case; served documents are named "Report 1", "Supplementary report 1", "Answers to questions 1", and so on.
- **Serving.** `report_servings` records each recipient, date and method; one served version can have several recipients (joint instruction). Deadlines use the service date on the party concerned; the expert can edit it.
- **Withdrawing a served report.** Never deleted. It is marked withdrawn with a reason through a supplementary statement, and stays locked.
- **Templates and signatures** may change between versions; snapshots keep the old ones.

### 10.7 Court

- **Court directions override pack defaults.** The expert records each direction (`court_directions`: date, reference, text, deadlines created). Deadline history (`deadline_changes`) keeps every extension and its source. A stay puts the case on hold.
- **Hearings.** `hearings` (type from the pack: trial, case management, inquest; start, end, venue, status scheduled/adjourned/vacated/heard). Multi-day hearings and adjournments keep their history. Trial packs and offline packages are built for a hearing, and are marked stale when new material arrives after they were built.
- **Settlement and reopening.** Settled cases can be reopened before deletion (state machines). Retention restarts from the latest settlement.
- **Written questions come in rounds.** `written_question_rounds` (received on, from which party, deadline). Answers per round become a served document; the jurisdiction pack says whether answers form part of the report (England and Wales: yes).
- **Expert meetings:**
  - Any number of participants (`expert_meeting_participants`: name, discipline, side, on Avodeck or not).
  - Agendas are versioned (`agenda_versions`).
  - Drafts exchanged outside Avodeck are imported as new versions with differences shown.
  - A document signed outside Avodeck is uploaded and recorded as `signed_external` with its hash.
- **Court bundles are versioned.** A re-issued bundle supersedes the previous one. Each version has its own concordance; the trial pack uses the latest confirmed one. Supplementary bundles are separate bundles with their own section prefix.
- **Expert evidence not permitted, or expert discharged:** case status `withdrawn` with that reason.

### 10.8 Money

- **Who issues invoices.** The organisation that owns the case, or the expert for personal cases. Each issuer has its own name, address and tax registration number, and its own gap-free invoice numbering.
- **Sent invoices are immutable.** Corrections are credit notes.
- **Payment status** is recorded by the expert (unpaid, part-paid, paid, written off). Each payment is its own row (`invoice_payments`); the paid amount is their sum, never more than the total less credit notes, and the status follows the sums. Writing off needs step-up. Overdue reminders are optional.
- **The invoice keeps its issuer.** The tax rate, name, address and tax number are copied onto the invoice when it is drafted; the PDF uses only those.
- **Fee caps** (compensation schemes, later) come from the case-type pack.
- **Avodeck's own subscription** is billed per organisation (seats) through Stripe and is separate from expert invoices.

### 10.9 System

- **Jobs:**
  - one job that changes a case runs at a time per case (others queue);
  - interactive work runs before batch work;
  - the expert can cancel bundle processing, which stops cleanly at a page boundary;
  - a job that fails three times goes to a dead-letter queue with an alert;
  - every job records the code version that ran it.
- **Scheduled jobs** run keyed by date ("deadline reminders for 2026-10-16"), so a missed run catches up exactly once. Deadlines are evaluated in the court's timezone as dates, so clock changes cannot move them.
- **Notifications.** `notifications` plus preferences per person per organisation; in-app and email channels; daily digest optional. A bounced email shows an in-app banner. Emails never contain patient data.
- **Feature flags** are scoped global, cell, organisation or person, and evaluated on the server.
- **Idempotency.** Creating, sending, serving and signing accept an `Idempotency-Key` header; a repeat returns the first result.
- **Search and embeddings** store the model that produced them. A model change triggers a re-index job; queries use only current rows.
- **Audit log versus deletion.** When a case is deleted:
  - its content goes, and its audit rows are kept unchanged as pseudonymised metadata for 6 years for security: they hold only IDs, short codes and hashes, and the IDs of deleted case data point at nothing (ADR 0015);
  - `page_reads` and `fact_verifications` are deleted with the case, but the final hash of each chain is written on the deletion certificate.
- **Legal hold.** `legal_holds` (case, reason, set by, until) blocks deletion; the retention job skips held cases and notifies the expert. Holds are set by the named expert or an organisation owner (step-up).
- **Deletion ledger.** Every deleted case ID is kept in `deletion_ledger`. After any backup restore, the ledger is re-applied before the service reopens, so restored backups cannot bring deleted cases back. The deletion job removes rows through `app.purge_case(case_id)` (in `PURGE_ORDER`, `api/app/core/contracts/case_tables.py`), which keeps the case row as a tombstone and the audit rows unchanged: they hold IDs and short codes only, and the hash chain covers every column, so rewriting them would break it.
- **Keys.** KMS keys rotate yearly (automatic); data keys are re-wrapped when next used.
- **Monitoring data retention:** logs 30 days, metrics 13 months, error reports 30 days.
- **API and package versions.** The API is `/v1`; breaking changes go to `/v2` with an overlap period. The frontend and API stay compatible one release apart. The offline package format is versioned; older apps refuse newer formats with a clear message.
- **Migrations.** Expand, migrate, contract; workers drain before the contract step.
- **Rate limits** per person, per organisation and per endpoint.

### 10.10 AI

- **Models.** Model IDs are pinned per cell with a version. When a provider announces a retirement: evaluate the replacement on all eval sets, then switch at a release. Earlier outputs keep their recorded model ID.
- **Prompts** are versioned in config and can be rolled back without a deploy.
- **Ground truth.** Changes to `evals/**/expected/` need sign-off by a clinician reviewer (CODEOWNERS). Real de-identified samples, only with consent, live in a private evaluation store outside the repository.
- **Drift.** Weekly rejection rates per AI task per pack combination; an alert fires when the rate rises 5 points above baseline.
- **No training on customer data.** Verification outcomes are used only as aggregate quality measures.
- **Fail closed.** No fallback to another region or provider.

### 10.11 Packs

- **Lifecycle:** draft → approved → active → deprecated → retired. Approval records the named legal or clinical reviewer and date in `pack.yaml` (`approved_by`, `approved_on`); production cells refuse unapproved packs.
- **Version pinning.** Cases pin pack versions at creation:
  - patch versions (x.y.Z, corrections) apply to unserved cases automatically, with a notice;
  - a correction marked `mandatory_update: true` (e.g. statement-of-truth wording) applies to all unserved cases and blocks signing until the expert acknowledges it;
  - minor and major versions apply only to new cases unless the expert opts in;
  - served reports never change.
- **Pack text is translatable** through i18n keys and locale files.

### 10.12 Data protection

- **Requests from the subject** (access, erasure) are forwarded to the controller (expert or organisation) within 2 working days. Avodeck provides the export. Erasure is the controller's decision and is subject to legal hold and court duties.
- **Breaches.** Avodeck notifies controllers without undue delay (target 24 hours) so they can meet the 72-hour duty.
- **Sub-processor changes:** 30 days' notice to controllers.

### 10.13 Using the product

- **Devices.** Sessions are per device; people can list and revoke them. The same case can be open in several tabs; optimistic concurrency (10.5) prevents silent overwrites.
- **Printing.** Key screens have print styles. Printed page images carry the case label and page number.
- **Screens for these rules** are on the canvas (`docs/spec/design-canvas.md`; file codes are the master numbering): organisation and members (`S27-OrgMembers`), account and security including devices, backup codes, message preferences, custodian and closure (`S28-Account`), court orders and hearings (`S29-CourtOrders`), not this patient's record (`S30-NotThisPatient`), and pause deletion on close the case (`S26-Close`). Existing boards show the other behaviour changes.

### 10.14 Phase-gate re-audit

Before the MVP, before each phase and before each new country or specialty, re-run `scripts/check_spec.py` and the behaviour audit questions (`.claude/skills/slice-brief/SKILL.md`, behaviour section) over anything added since the last gate.
