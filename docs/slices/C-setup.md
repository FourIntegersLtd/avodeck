# Slice C: Case setup

Owner: Claude session, branch `slice/c-setup` · Phase: MVP · Migrations 0300–0399

## Goal
The expert sets up their profile once, then for each case enters the details, declares conflicts, records oral instructions, and confirms the exact questions they've been asked, pulled from the letter of instruction.

## Screens
- 02 Your expert profile (`S02-Profile`)
- 04 New case (`S04-NewCase`): case details and oral instructions (uploads from foundation)
- 05 Before you start (`S05-Conflicts`)
- 06 What you've been asked (`S06-Questions`)
- Today cards: registers "questions to confirm" and "profile incomplete" via `NeedsYouItem`

## Endpoints (architecture 5.2)
- Profile: `GET/PATCH /v1/me/profile`; `GET/POST /v1/me/roles`, `PATCH/DELETE /v1/me/roles/{id}`; `GET/POST/DELETE /v1/me/training[/{id}]`
- Template: `POST /v1/me/template/file`; `PATCH /v1/me/template/mapping`
- `GET/PUT /v1/me/declaration`; `GET /v1/jurisdictions/{code}` (fixed wording from the pack, foundation-owned)
- Signature: `POST /v1/me/signature`; `GET /v1/me/signature/image`
- `GET /v1/me/stats/balance?months=12`
- `POST /v1/cases/{id}/prefill` → 202; `GET /v1/cases/{id}/prefill`; `PATCH /v1/cases/{id}/prefill/{suggestion_id}` `{decision}`; `PUT /v1/cases/{id}/oral-instructions`
- `GET/PUT /v1/cases/{id}/conflicts`
- `GET /v1/cases/{id}/questions`; `PATCH /v1/cases/{id}/questions/{q_id}`; `PATCH /v1/cases/{id}/assumptions/{a_id}`; `POST /v1/cases/{id}/questions/confirm-all`
- `POST /v1/cases/{id}/instructing-party-notes/draft`

## Tables owned
`expert_roles`, `expert_training`, `report_templates`, `declarations`, `signatures`, `case_instructions`, `conflict_answers`, `assumptions`. Fills the `questions` contract table. Chunk B added `conflict_declarations`, `case_withdrawals` (0320), `instruction_extractions` (0330), `prefill_suggestions`, `prefill_runs` (0340); all registered in `core/contracts/case_tables.py`.

## Worker jobs: `extract_questions`, `prefill_case`
`extract_questions`: OCR the letter of instruction (T-07a kit) → `pii.strip` → **T-06** → exact-substring check → `questions` and `assumptions` rows with status `suggested`. Queued by `POST /instructions` when a document is attached.

`prefill_case` (chunk B): OCR the latest letter → `pii.strip` → **T-04** → checks (a name is exactly the cited words; a key is one of the pack's; a due date is written out in the cited words, read with the pack's date order and language) → `prefill_suggestions` (encrypted) waiting for the expert. Accepting only records the choice; the case changes when the expert saves the screen 04 form.

## AI tasks (architecture 6.3)
| Task | Model | Output | Check |
|---|---|---|---|
| T-02 (optional) | Sonnet 5 | heading → report section map | expert confirms |
| T-04 (optional) | Azure DI + Sonnet 5 | `{instructing_party_name, instructing_party_type, party_role, due_date, court_track?}` with `word_ids` | expert confirms each field |
| T-06 | Azure DI + Sonnet 5 | `questions[{number, text_verbatim, word_ids, para_ref, kind, outside_expertise, reason}]`, `assumptions[]` | exact substring of the letter; expert confirms each |
| T-DICT | Azure Speech (foundation kit) | oral instructions transcript | expert edits |

## Contracts
- **In:** foundation auth, uploads, jobs, AI kit, dictation kit.
- **Out:** confirmed `questions` (used by D, E, F, G), expert profile, roles and template (F), signature (F), declaration (F), conflicts text (F).

## Acceptance tests (write first)
- [ ] Roles store date ranges; conflicts screen warns when no role in the expert's specialty (settings from the specialty pack) covers the case period (2024-02-01 to 2024-09-30 in the fixture, from `evals/.../expected/case.json`)
- [ ] Uploading the fixture `.docx` template stores the original unchanged (same SHA-256) and returns its heading map
- [ ] Declaration can be edited until the expert's first report is served, then returns 409
- [ ] The statement of truth and declaration wording shown come from the `gb-eaw` pack and cannot be changed by the expert
- [ ] Drawn signature PNG and photo upload both store; background removed; served only through the API
- [ ] T-06 on the fixture letter returns 4 questions and 1 assumption whose text is an exact substring of the letter with correct `para_ref`
- [ ] A recorded T-06 response that paraphrases a question is rejected
- [ ] Question 4 (causation) is flagged `outside_expertise` because the case-type pack marks causation as usually outside the general-practice specialty
- [ ] Writing cannot start (409) until `confirm-all` has been called; edits keep the original text
- [ ] Conflict answers generate the declaration paragraph; "Yes" answers require detail
- [ ] Balance stats return defendant/claimant/joint counts for the last 12 months
- [ ] Oral instructions save as a dated record from dictation or typing
- [ ] Prefill suggestions (T-04) are never applied without the expert confirming
- [ ] Screens 02, 04, 05, 06 pass Playwright + axe and match their canvas boards

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Test inputs:** synthetic files in `bundles/synthetic-mrs-a/inputs/` (built by `make eval-data`), expected results in `evals/gb-eaw/clin-neg-breach/general-practice/expected/inputs.json` under `instruction_version_2`.

**Extra tables owned:** `case_parties`, `case_instructing_parties`, `case_links`, `case_instructions` (oral wording), `case_subject_keys` (migration 0310). `instruction_versions` is a foundation table (0017) that this slice fills. Screens for `case_members` (table from foundation).

**Extra endpoints:** `GET/POST /v1/cases/{id}/members`, `DELETE /v1/cases/{id}/members/{member_id}`, `GET/POST /v1/cases/{id}/parties`, `PATCH/DELETE /v1/cases/{id}/parties/{party_id}`, `GET/POST /v1/cases/{id}/instructing-parties`, `PATCH /v1/cases/{id}/instructing-parties/{ip_id}`, `GET/POST /v1/cases/{id}/instructions`, `GET/POST /v1/cases/{id}/links`, `DELETE /v1/cases/{id}/links/{link_id}`, `POST /v1/cases/{id}/conflicts/declare`, `POST /v1/cases/{id}/withdraw` 🔒, `POST /v1/cases/{id}/hold`, `POST /v1/cases/{id}/resume`, `PATCH /v1/cases/{id}/jurisdiction` 🔒, `POST /v1/cases/duplicate-check`; added in chunk A: `PUT /v1/cases/{id}/subject-key` (stores the keyed hash the duplicate check compares) and `GET /v1/cases/{id}/subject` (the subject's words for the current status).

- [ ] New case asks "Who is this case for?" and sets `organisation_id` and `named_expert_id`
- [ ] Any number of parties with pack role keys; a joint expert has two instructing parties and every draft is addressed to both (S4)
- [ ] A supplementary letter creates an instruction version; a new question reopens a locked set with a warning; links and opinions stay (S7)
- [ ] A conflict declared mid-case drafts a note; withdrawal sets status `withdrawn`, keeps served reports locked and cancels deadlines (S16)
- [ ] Duplicate-case warning compares a keyed hash of the subject identifier; the identifier is never stored
- [ ] Jurisdiction can change only before serving (409 after), with packs re-stamped and changes listed
- [ ] On hold pauses reminders and queues jobs; resume restores them (chunk A: hold/resume set `cases.on_hold`; no reminder or job code reads the flag yet, so pausing waits for the slices that own reminders and the job queue)
- Open: `docs/spec/behaviour-index.md` says parties are "editable until served; then additions only". Chunk A refuses new parties after serving (409) and the database refuses changes and removals; allowing additions after serving needs a founder decision because the committed test asserts 409.
- [ ] Subject status (living, deceased, minor) changes only vocabulary, proven under the fixture packs

## Chunk B notes (2026-10-17)
- T-02 (built in chunk C): `POST /v1/me/template/suggestions` suggests a report section per heading from the pack's report sections plus `other`; it runs on request, is never saved, and the expert saves the map with `PATCH /v1/me/template/mapping`.
- **Gap, deadlines on withdraw:** `POST /withdraw` returns `deadlines_cancelled: 0`. Deadlines belong to the slices that set them; they must read the case's `withdrawn` status.
- **Gap, retention on withdraw:** withdraw does not set `delete_after` (it needs `case.retention`, slice H's action); slice H starts the retention clock from `case_withdrawals.created_at`.
- **Gap, i18n:** the keys the API returns (`setup.conflicts.warning.coverage_{none,partial}`, `setup.conflicts.question.<key>`, `setup.questions.warning.*`, `setup.today.{questions_to_confirm,profile_incomplete}[_action]`, note `label_key`s) go into `web/src/foundation/i18n/en-GB.json` with the screens (chunk C).
- **Gap, evals:** no `evals/.../expected/setup-*.json` rows yet; T-04 and the conflicts expectations are in the recorded fixtures and tests (`api/tests/fixtures/setup/record_setup.py`).
- Conflicts: the paragraph is the pack's `statement.none`, or `statement.disclosed` followed by the expert's details. Coverage counts roles whose `setting` is one of the specialty's `coverage_roles` (covered/partial/none/unknown); roles stay private to the expert.
- Today: "questions to confirm" per case for the named expert (screen 06); "profile incomplete" once, on the first case shown, while the name, profession, registration body or number, or a specialty is missing (screen 02).

## Chunk C notes (2026-10-17)
- Screens built: S02 at `/profile`; S04 at `/cases/new` (new case) and `/cases/{id}/details` (letter upload and prefill, case details, who instructed you, same person check, oral instructions); S05 at `/cases/{id}/conflicts` (including declaring a change and stepping back with step-up); S06 at `/cases/{id}/questions`. Vitest under the launch and fixture packs; Playwright with axe, buttons at least 52px and text at least 17px in `web/e2e/setup/`.
- `GET /v1/me/template` gains `sections` (the pack report sections across the cell, plus `other`), because a heading is mapped outside any case.
- Prefill "Use this" only fills the form; the case changes when the expert saves. A second letter is sent as kind `supplementary`.
- Screen keys registered: `S02`, `profile`, `new_case`, `S04`, `case_details`, `S05`, `conflicts`, `S06`, `questions`. The header's Your profile link now shows (foundation reset the registry for the shell's own tests).
- **Gap, i18n:** `setup.conflicts.question.<key>` keys are not added; the screen shows the pack's own question text.
- **Gap:** the drawn-signature path is tested only in the browser build (jsdom has no canvas); board fidelity was checked by eye against the PNGs.

## Rules that apply
1, 2, 4, 5, 6, 7, 9 from the root `AGENTS.md`.

## Out of scope
Uploading mechanics (foundation), bundle processing (A), report assembly using the template (F).

## Definition of done
Root checklist, plus: a test expert can complete the profile and reach confirmed questions on the fixture letter on staging.
