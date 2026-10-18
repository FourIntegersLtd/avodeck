# Slice G: After serving

Owner: Claude session, branch `slice/g-after-serving` · Phase: 2 · Migrations 0700–0799

## Goal
After the report is served, the expert keeps track of court deadlines and answers written questions from the other side within scope. New records go through the same blind-read process. Every claim in the other expert's report is checked against the actual pages. Corrections and changes of opinion become a supplementary report. The served report is never altered.

## Screens
- `S29-CourtOrders` Court orders and hearings (canvas board 31)
- 19 After serving (`S19-Served`): deadlines timeline, locked served version
- 20 Questions from the other side (`S20-Questions35`): pack label "Part 35 questions"
- 21 New records and the other expert's report (`S21-NewMaterial`)
- 21b Correct or update your report (`S21b-Addendum`)
- 29 Court orders and dates (`S29-CourtOrders`): court directions, hearings, deadlines and their history; a stay puts the case on hold
- Today cards: registers "written questions due", "new pages to read" and "other expert's report to check"

## Endpoints (architecture 5.2)
- `GET /v1/cases/{id}/deadlines`; `POST/PATCH /v1/cases/{id}/deadlines[/{id}]`; `GET /v1/cases/{id}/deadlines/{id}/changes`; `GET/POST /v1/cases/{id}/court-directions`; `GET/POST /v1/cases/{id}/hearings`; `PATCH /v1/cases/{id}/hearings/{id}`; `GET /v1/cases/{id}/report/served`
- `GET/POST /v1/cases/{id}/written-question-rounds`
- `POST /v1/cases/{id}/written-questions/import` → 202; `GET /v1/cases/{id}/written-questions`; `PATCH /v1/cases/{id}/written-questions/{q_id}`; `POST …/{q_id}/tell-instructing-party`; `POST /v1/cases/{id}/written-questions/answers/sign` 🔒
- `POST /v1/cases/{id}/documents/append` (A's route, reused); `GET /v1/cases/{id}/new-since-served`; `POST /v1/cases/{id}/new-since-served/check` → 202
- `POST /v1/cases/{id}/opposing-reports` → 202; `GET /v1/cases/{id}/opposing-reports/{doc_id}`; `PATCH /v1/cases/{id}/opposing-claims/{claim_id}`
- `POST/GET /v1/cases/{id}/report-changes`; `GET/PATCH /v1/cases/{id}/report-changes/{change_id}`; `POST /v1/cases/{id}/report-changes/supplementary` → 202; `POST /v1/cases/{id}/report/versions/{v}/sign` 🔒

## Tables owned
`deadlines` (rule_ref from the pack), `deadline_changes`, `court_directions`, `hearings`, `written_question_rounds`, `written_questions`, `opposing_reports`, `opposing_claims`, `report_changes`, `may_matter` (fact_id, question_id, why, seen_at).

Also writes, for its own documents only: `report_versions` and `report_snapshots` rows of type `written_answers` and `supplementary` (never a served row; the database refuses with `AVLCK`), and `cases.on_hold` when a court direction stays the case.

## Worker jobs
- **`verify_opposing`:**
  1. Azure DI Read on the other expert's report.
  2. `pii.strip`, then T-21b claims.
  3. For each claim, code checks first: does the cited page exist, and is the cited value in its words?
  4. Only unresolved claims go to T-21c on Opus. Then T-21d positions.
- **Deadline reminders:** a daily scheduled job emails via Resend ("A deadline in case Mrs A is in 3 days"; case label only).
- **Supplementary report:** calls F's `build_report` with `type = supplementary` and `prev_version_id`.

## AI tasks (architecture 6.3)
| Task | Model | Output | Check |
|---|---|---|---|
| T-20a Extract written questions | Azure DI + Sonnet 5 | Same schema as T-06 | Exact substring; expert confirms |
| T-20b Classify and link | bge-small + Sonnet 5 | `{question_id, kind: clarifies\|goes_beyond, paragraph_ids, reason}` | Legal judgement stays with the expert; shown as a suggestion |
| T-21a May matter | bge-small + Sonnet 5 | `{fact_id, question_id, why}` | Blind read still applies; the expert reads every new page anyway |
| T-21b Other expert's claims | Azure DI Read + Sonnet 5 | `claims[{text_verbatim, word_ids, cited_page, cited_value}]` | Exact substring of their report |
| T-21c Verify claims | Code, then Opus 5.5 | `{status: matches\|wrong_page\|wrong_value\|not_on_page\|contradicted, evidence_word_ids}` | Both quotes shown; the expert confirms each discrepancy |
| T-21d Compare positions | Sonnet 5 | `{question_id, own_paragraph_ids, their_paragraph_ids}` | Verbatim side by side, no summary |
| T-21e Dependent paragraphs | Code (shared citations), then Sonnet 5 | `{paragraph_id, depends, reason}` | The expert reviews each |

## Contracts
- **In:** the locked served version and the `report_served` event (F), appended-page processing (A), blind read and verification (B), `citable_facts()`, the sign flow and `build_report` (F), pack deadlines (foundation).
- **Out:** written answers and supplementary versions (I's trial pack), updated positions per question (I's joint statement), deadlines (Today).

## Acceptance tests (write first)
- [ ] On `report_served` (and as a backfill for cases served before G existed), a deadline is created from the pack: served_on + 28 days for written questions under `gb-eaw` (calendar days, Europe/London); the fixture jurisdiction gives 21 business days in its own timezone, with `rule_ref` from the pack. The expert can edit dates from court directions.
- [ ] The served version is shown read-only. Any attempt to edit it returns 409.
- [ ] T-20a on the synthetic questions document returns 6 questions as exact substrings. The planted question asking for a new opinion on causation is suggested as `goes_beyond`, and "Tell the instructing party" (pack label "Tell the solicitor") drafts a note with no patient identifiers.
- [ ] Answers can cite only `citable_facts()`. Signing the answers needs step-up and produces a locked version.
- [ ] Appending 14 new pages after serving numbers them after the last page (901–914 in the fixture). Their facts are behind blind read (423 until read). "New since served" lists them, and T-21a flags the planted relevant result.
- [ ] Synthetic other expert's report, 12 claims. Code alone resolves the 7 that match and the 2 citing a page that doesn't exist. Opus is called only for the remaining 3: one wrong value (they quote "ferritin 19", page 402 says 9), one contradicted, one matches.
- [ ] Every status shows both quotes with page numbers. Nothing is called a discrepancy until the expert confirms it.
- [ ] T-21d returns paragraph IDs only. The screen shows verbatim text side by side with no generated summary (schema test).
- [ ] Changing a paragraph creates a `report_changes` row (correction or change of opinion, with a reason). T-21e lists the dependent paragraphs that share its citations.
- [ ] The supplementary report contains only the changes, their reasons and the declaration. It links to the served version via `prev_version_id`, needs step-up to sign, and leaves the served version byte-identical (hash unchanged).
- [ ] Reminder emails contain only the case label and the deadline kind, never patient details.
- [ ] Screens 19, 20, 21 and 21b pass Playwright + axe and match their canvas boards.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Test inputs:** synthetic files in `bundles/synthetic-mrs-a/inputs/` (built by `make eval-data`), expected results in `evals/gb-eaw/clin-neg-breach/general-practice/expected/inputs.json` under `court_order`, `written_questions_round_2`.

**Extra tables owned:** `court_directions`, `deadline_changes`, `hearings`, `written_question_rounds`.

**Extra endpoints:** `GET/POST /v1/cases/{id}/court-directions`, `GET /v1/cases/{id}/deadlines/{deadline_id}/changes`, `GET/POST /v1/cases/{id}/hearings`, `PATCH /v1/cases/{id}/hearings/{hearing_id}`, `GET/POST /v1/cases/{id}/written-question-rounds`.

- [ ] A court direction overrides the pack default; every deadline move is kept with its source (S10)
- [ ] Adjourning a hearing links the new date and marks trial packs and offline packages stale
- [ ] Each round of written questions has its own deadline and answers document (S11)
- [ ] Deadlines are dates in the court's timezone; clock changes cannot move them
- [ ] A stay puts the case on hold

## Rules that apply
1, 2, 3, 4, 6, 7, 8, 9, 10, 11 from the root `AGENTS.md`.

## Decisions taken while building
- The answers document and the signed supplementary file are built inside the sign request (small Word files, no AI); the draft supplementary is a 202 job (`build_supplementary`).
- Deadline kinds come from the pack; answers have their own kind `written_question_answers`. Reminder emails carry `deadline.<kind>` only and go three days ahead, once per deadline (`last_reminded_on`).
- The `report_served` event handler runs after F commits and makes the pack's deadlines counted from serving, acting as `ReportServed.served_by` (set by F). An event naming nobody is refused, never guessed.
- G's security-definer functions accept `app.job_case_id` only from `session_user = 'app_worker'`, as migration 0027 does.
- Withdrawing a case (slice C) publishes `CaseWithdrawn`; G cancels its open deadlines (`cancelled_at`, migration 0701) and C reports the count.
- S20 takes the question types from the case pack (`written_question_classifications`); S29 opens under the pack's `court_orders` screen.
- S20 and S21 take the document id from the URL until there is a documents list for these screens; there is no list of opposing reports yet.
- Job kinds `find_dependents` and `build_supplementary` need `opinion.write` (`job_kind_actions` rows in migration 0701).

## Out of scope
Expert meetings and joint statements (I), trial bundles (I), Gmail/Outlook sending (separate Phase 2 item in slice A).

## Definition of done
Root checklist, plus: on staging the synthetic served case receives questions, new records and the other expert's report, and the expert produces a signed supplementary report without the served version changing.
