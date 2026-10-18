# Slice D: Understand

Owner: Claude session, branch `slice/d-understand` · Phase: MVP (screen 12 basic) · Migrations 0400–0499

## Goal
The expert sees the whole story on a timeline, opens any consultation exactly as the clinician saw it on that day (later notes hidden, guidance in force shown), and works through a short list of "Things to look at" that code has found. They decide each one; the computer never decides.

## Screens
- 11 The timeline (`S11-Timeline`): lanes from the pack, filters (date, status, question), links to questions
- 12 One consultation, as the clinician saw it (`S12-Consultation`): label from the packs: "as the GP saw it" under gb-eaw/general-practice
- 13 Things to look at (`S13-ThingsToLook`): findings with evidence pages (a decided finding no longer found stays listed, marked so); links to the questions (suggest, confirm, reject, add by hand, stale list); check settings (on/off and days allowed). Buttons follow `Case.allowed_actions`
- Today cards: registers "things to look at to decide"

## Endpoints (architecture 5.2)
- `GET /v1/cases/{id}/timeline?from=&to=&status=&question_id=`
- `GET /v1/cases/{id}/fact-links`; `POST /v1/cases/{id}/fact-links/suggest` → 202; `PATCH /v1/cases/{id}/fact-links/{link_id}` (If-Match; a `stale` link gives 409 `stale_link`); `POST /v1/cases/{id}/fact-links` (confirmed questions only, through `confirmed_questions`)
- `GET /v1/cases/{id}/consultations/{fact_id}?as_seen=true&on=` (`on` shows the record as it stood on another day; the reply carries `guidance_changed`)
- `PATCH /v1/cases/{id}/facts/{fact_id}/availability` `{available_at | unknown, reason}`
- `PUT /v1/cases/{id}/consultations/{fact_id}/note`
- `GET /v1/guidelines/in-force?code=&date=` (jurisdiction taken from the case or query; global table)
- `GET /v1/cases/{id}/findings`; `PATCH /v1/cases/{id}/findings/{finding_id}` `{decision, reason}`
- `GET/PATCH /v1/me/checks`; `POST /v1/cases/{id}/findings/rerun` → 202
- `POST /v1/me/checks/custom` (later; returns 501 in MVP)

## Tables owned
`fact_question_links`, `check_config`, `findings`, `consultation_notes`, `availability_overrides` (fact_id, available_at, unknown, reason, at; append-only), `check_runs` (one row per run of the checks), `case_guideline_versions` (the guideline version each case was shown). Guideline versions are read from the overlay pack's `guidelines.yaml` (`pack.guidelines()`) rather than a seeded `guideline_versions` table; a case records the version it was shown in `case_guideline_versions` (ADR 0016).

Writes on the contract table: `facts.available_at`, `facts.availability_basis`, computed by code.

## Code, not AI
- **Availability:** for each fact, `available_at` = the earliest `availability_evidence` date (received, filed, seen, sent to the clinician) from T-07b. Otherwise `unknown`. Never the event date of a letter, and never decided by the model.
- **As seen on a date:** visible = verified or suggested facts whose `available_at` ≤ the consultation date. Later entries are counted but hidden behind "Show what came later". Unknown is shown as unknown.
- **Guidance in force:** a version in the overlay pack's guideline table (ADR 0016) where `in_force_from` ≤ date < `in_force_to`. Show the version label and link only. **No guideline text goes to any AI** until the licence allows it (decision 0005).
- **Checks engine:** rules come from the packs (`checks.yaml` in the specialty pack, plus the expert's `check_config`). Each rule is code over facts: kinds, time windows, conditions. T-13a only labels the text spans a rule points at.

## AI tasks (architecture 6.3)
| Task | Model | Input | Output | Check |
|---|---|---|---|---|
| T-11 Link events to questions | bge-small → bge-reranker → Sonnet 5 | Confirmed questions + candidate verified facts | `{fact_id, question_id, relevant, reason}` | Suggested links only; the expert confirms. Runs only on verified facts |
| T-13a Action / safety-netting label | Sonnet 5 | The spans a rule found + the rule | `{label: action_taken\|no_action\|safety_netting\|none, word_ids}` | Value-in-span; the finding still needs the expert's decision |

## Contracts
- **In:** verified facts and `citable_facts()` (B), confirmed questions (C), `availability_evidence` (A), pack lanes, checks and guidelines (foundation). Stub B with `stub_facts.py` marked verified.
- **Out:** confirmed links (E's points and opinion workspace, G's T-21a), findings and decisions (F's final checks), `available_at` (E and F).

## Acceptance tests (write first)
- [x] Timeline lanes and their order come from the case's pack (`lanes.yaml`). Changing the pack fixture changes the lanes with no code change.
- [x] Facts show their status (Checked by you / Please check) and filter by status, date range and linked question.
- [x] Synthetic case: the consultation on page 118 (15 May 2024). The hospital letter dated 10 May but stamped received 22 May is hidden "as seen" and listed under "What came later". The blood result filed 14 May is visible.
- [x] A letter with no availability evidence shows "Not known when this arrived". It is never placed before the consultation automatically.
- [x] Overriding availability requires a reason, appends to `availability_overrides`, and is audited. The latest override wins.
- [x] `GET /guidelines/in-force?code=NG12&date=2024-05-15` returns the version in force on that date from the overlay pack's guideline table. A date before the first version returns `not_found`.
- [x] Recorded AI requests from this slice never contain guideline text beyond the code and title.
- [x] T-11 is refused for unverified facts and only suggests links. Confirming or rejecting is audited. Manual links can be added.
- [x] Planted case: an abnormal result (planted ferritin 9 µg/L on page 402) with no action within 14 days produces an `abnormal_result_no_action` finding with evidence pages.
- [x] Turning that check off in `/me/checks` affects only new runs. `rerun` recreates findings and keeps decisions on findings that still exist.
- [x] `gentle` checks use the softer wording keys from i18n.
- [x] A decision of `not_relevant` or `explained_elsewhere` requires a reason. Findings are never auto-closed.
- [x] Planted referral with no acknowledgement within 6 weeks produces `referral_acknowledged`. A planted out-of-hours contact that reached the clinician the next day produces no finding.
- [x] No slice code contains a clinician title, guidance body or a fixed lane list (core scan), and the timeline renders the fixture specialty's lanes unchanged.
- [ ] Screens 11, 12 and 13 pass Playwright + axe (axe passes in the component tests; the Playwright run needs `make dev` and was not run in the 2026-10-18 audit) and match their canvas boards. The timeline renders 1,500 events in under 1 s.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `comments`.

**Extra endpoints:** `GET/POST /v1/cases/{id}/comments`, `PATCH /v1/cases/{id}/comments/{comment_id}`.

- [x] Any case member can comment on a page, fact or paragraph; comments never appear in a report; the screen warns they may be disclosable
- [x] Links whose fact is rejected, or whose question is removed, become `stale` and are shown for review (a removed question sets `question_id` to null and the row to `stale`; a link whose fact was later rejected reads as `stale` in `/fact-links` but its row stays `confirmed`, because slice D cannot react to slice B's fact changes; `core/contracts/fact_links.confirmed_links` returns `confirmed` rows only and should also keep to checked facts, raised with foundation on 2026-10-18)
- [x] A change to a cited guideline's table entry shows a notice on the case; the case records the table version used (`case_guideline_versions` with `entry_hash`; "Check again" takes the new list)

## Rules that apply
1, 2, 4, 6, 7, 9, 11 from the root `AGENTS.md`.

## Out of scope
Fact verification (B), the custom check builder T-13b (later), sorting points for and against (E), causation or condition-and-prognosis views (slice J).

## Definition of done
Root checklist, plus: on staging the synthetic case shows the two planted findings (and not the out-of-hours one), the page 118 "as seen" view is correct, and the expert can decide each finding.
