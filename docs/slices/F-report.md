# Slice F: Report and sign

Owner: Claude session, branch `slice/f-report` · Phase: MVP · Migrations 0600–0699

## Goal
Avodeck assembles the expert's report in their own Word template, with the court's required contents, a methodology appendix, and every citation linked to a checked fact on a page they read. A final check runs 28 checks. The expert then signs with the declaration from the pack, the version is locked and fingerprinted, and they mark it served. Time is tracked quietly so they can send an invoice.

## Screens
- 16 Your report (`S16-Report`): sections, compliance checklist, page count, citation style
- 17 Final check (`S17-FinalCheck`): results with "Fix it" links; optional colleague spot-check
- 18 Sign and send (`S18-SignSend`): signature, declaration, lock, download, mark served
- 18b Time and invoice (`S18b-Invoice`)
- Today cards: registers "report ready to check", "final check failed" and "invoice not sent"

## Endpoints (architecture 5.2)
- `POST /v1/cases/{id}/report/build` → 202; `GET /v1/cases/{id}/report`; `GET /v1/cases/{id}/report/preview/{page}`; `GET /v1/cases/{id}/report/paragraphs?section=` (text of the built paragraphs, read-only; the report's `sections[].done/total` count answered questions on the opinion section; each version carries `title` (the case type pack's `report_name`, a colon and the case label, else the version name) and the report carries `court_track_label` from the jurisdiction vocabulary `court_tracks`); `PATCH /v1/cases/{id}/report/settings`; `GET /v1/cases/{id}/report/methodology`
- `POST /v1/cases/{id}/report/word-import` (Phase 2; returns 501 in MVP)
- `POST /v1/cases/{id}/report/final-check` → 202; `GET /v1/cases/{id}/report/final-check/latest` (a result may add `checked`, `total`, `unit` for passed checks and `explain`, the pack's line for a failed pack check)
- `POST /v1/cases/{id}/spot-checks`; `GET/POST /v1/spot-checks/{token}[/responses]` (no login; expiring token plus emailed code)
- `POST /v1/cases/{id}/report/sign` 🔒; `GET /v1/cases/{id}/report/versions/{v}/download?format=docx|pdf`; `POST /v1/cases/{id}/report/versions/{v}/serve` 🔒
- `POST /v1/cases/{id}/time/heartbeat`; `GET /v1/cases/{id}/time`; `PATCH /v1/cases/{id}/time/adjust`; `POST /v1/cases/{id}/time/estimates`
- `POST /v1/cases/{id}/invoices`; `POST /v1/cases/{id}/invoices/{inv_id}/send`

## Tables owned
`report_paragraphs`, `citations`, `final_check_runs`, `spot_checks`, `spot_check_address_tries`, `time_entries`, `invoices` (rate, total, currency from the expert profile). Writes the `report_versions` contract table (foundation owns locking at database level). Adds `paragraph_id` rows to E's `lint_issues` for whole-report lint.

## Worker jobs
- **`build_report`:**
  1. Load the expert's template and heading map (C).
  2. Take sections from the case-type pack's `report_sections.yaml` and required contents from the jurisdiction pack's `report.yaml`.
  3. Fill content controls with python-docx / docxtpl: opinions with citations (E), chronology from verified facts, documents list, conflicts declaration (C), and the methodology appendix (built from `ai_calls` metadata, `page_reads` counts and `fact_verifications` counts; model IDs, regions, prompt versions and dates, never content).
  4. Render the PDF with LibreOffice headless in the worker image.
  5. Store both files and create a `report_versions` row of type `draft`.
- **`final_check`:** 26 code checks plus T-17. The list is the core checks plus `final_checks.yaml` from the case-type and jurisdiction packs.

## Final checks (code unless marked)
Core checks include:
- every question answered or deferral explained;
- every citation resolves to a verified fact on a read page;
- every quote matches verbatim;
- all pages read;
- no unresolved findings (D) left undecided;
- points against considered for each breach question (pack);
- guidance cited was in force on the date of care;
- the subject's age agrees with date of birth and event dates;
- conflicts declared;
- expert details complete;
- page limit for the track (pack);
- the statement of truth is present with the exact pack wording (pack);
- required contents are present (pack);
- dismissed lint issues all have reasons.

**T-17** runs T-15a over the whole report (tone and expertise). It produces warnings, never edits.

## AI tasks (architecture 6.3)
| Task | Model | Input | Output | Check |
|---|---|---|---|---|
| T-02 heading map | (C's) | — | — | Read only |
| T-17 Tone and expertise | Sonnet 5 | Whole report paragraphs | Same schema as T-15a | Warnings only; the expert dismisses with a reason or fixes it in screen 15 |

## Contracts
- **In:** opinions and citations (E), template, profile, signature, declaration and conflicts (C), `citable_facts()` and page-read log (B), findings (D), questions (C), pack sections, checks and declarations (foundation).
  Each need maps to a `core/contracts` function (run on the caller's connection, API or the case's job):
  - Template and heading map (C): `profile.report_template(conn, case_id)`: the named expert's latest template (`storage_path`, `sha256`, `heading_map`, `style_map`, `mapping`). Needs `report.build` or the case's job.
  - Declarations (C): `profile.declarations(conn, case_id)`: the named expert's wording (`kind`, `text`, `locked_at`) for the case's jurisdiction; the fixed wording comes from the jurisdiction pack.
  - Signature (C): `profile.signature(conn, case_id)`: the newest signature (`storage_path`, `sha256`, `method`, `width`, `height`). Needs `report.sign` or the case's job.
  - Conflicts declaration (C): `profile.conflict_declaration(conn, case_id)`: latest `outcome`, `declared_at`, `out_of_date` and the answers (detail sealed under the case key).
  - Methodology appendix: `ai_calls.ai_call_summary(conn, case_id)`: per task, model ID, region and prompt version, the number of calls and first and last time; no content.
  - Verified facts and the read log (B): `facts.citable_facts()`, `reading.page_read_log` and `reading.verification_counts`; questions (C): `questions.confirmed_questions`; case people: `members.case_member_names`.
  - Opinions (E): `opinions.latest_opinions(conn, case_id, crypto)` (never `app.opinions` directly).
  - Citations (E): `citations.citation_problems(conn, case_id, reads)` in the final checks; signing is blocked while it returns any problem.
- **Out:**
  - A **locked served `report_versions` row** with SHA-256 of the DOCX and PDF. G, I and H depend on it.
  - The `report_served` event in `core/contracts/events.py`: `{case_id, version_id, served_on}`. G subscribes to create deadlines; in the MVP nothing subscribes.
  - Invoices (H exports them).
  - Time totals.

## Acceptance tests (write first)
- [ ] Building with the fixture template keeps the expert's styles (paragraph and heading style names unchanged), and section order follows the confirmed heading map.
- [ ] Every citation in the DOCX renders as page (or paragraph, per settings) and maps to a `citations` row whose fact is in `citable_facts()`.
- [ ] Changing citation style triggers a rebuild and creates a new draft version. Older drafts are kept.
- [ ] The methodology appendix lists each AI task used with model ID, region, prompt version and date, plus pages read / total. It contains no prompt or response text.
- [ ] Final check on the synthetic case, with one planted uncited date, one unread page and one undecided finding, fails exactly those three checks. Each has a "Fix it" target (screen and item).
- [ ] The statement of truth check fails if the text differs from `declarations.md` by one word. The expert cannot edit the wording.
- [ ] `sign` returns 403 `step_up_required` without recent MFA, and 409 if the latest final check did not pass or is older than the latest draft.
- [ ] Signing sets `locked_at`, stores SHA-256 of both files and writes to the audit log.
- [ ] Any later change to that row returns 409 (API) and fails at database level (core test).
- [ ] `serve` (🔒) records `served_on` and `sent_via`, sets case status `served`, and emits `report_served`.
- [ ] Downloads stream with `no-store` and are audited.
- [ ] Spot-check: 10 random cited facts. The colleague sees watermarked page crops only (no full pages, no patient name), the link expires, a code is emailed, and responses are stored.
- [ ] Heartbeat counts only when the tab is visible and there was input in the last 2–5 minutes. Stage comes from the current screen. Adjustments are audited.
- [ ] The invoice PDF uses the expert's rate and currency. It is emailed via Resend with no clinical content (only the case reference and amount).
- [ ] T-17 output schema has no free-prose field. T-17 never changes paragraph text.
- [ ] No slice code contains rule names or declaration wording (core scan); with the fixture jurisdiction the checklist, declarations and page limits change accordingly.
- [ ] Screens 16, 17, 18 and 18b pass Playwright + axe and match their canvas boards.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `report_snapshots`, `report_servings`, `credit_notes`, `invoice_payments` (insert-only, no case_id: purged with its invoice), `report_settings`, `report_section_texts`, `report_builds`; personal `billing_profiles` (team ones in O). Colleague spot-check routes: `GET /v1/spot-checks/{token}` and `POST /v1/spot-checks/{token}/responses`.

**Extra endpoints:** `GET /v1/cases/{id}/report/versions/{v}/snapshots`, `GET/POST /v1/cases/{id}/report/versions/{v}/servings` 🔒, `POST /v1/cases/{id}/report/versions/{v}/withdraw` 🔒, `PATCH /v1/cases/{id}/invoices/{invoice_id}/payment`, `POST /v1/cases/{id}/invoices/{invoice_id}/credit-notes` 🔒, `PUT /v1/me/billing-profile/bank-details` 🔒, `GET/PUT /v1/organisations/{org_id}/billing-profile/bank-details` 🔒 (PUT).

- [x] Signing freezes CV, declarations, signature, template, pack, guideline-table and instruction versions and conflict answers; a later profile change doesn't alter the signed report
- [x] Served documents are named from the pack ("Report 1", "Supplementary report 1"…); drafts numbered per case
- [x] One serving row per recipient; deadlines use each service date (S4)
- [x] A served report can be withdrawn with a reason, stays locked and readable, and nothing builds on it
- [x] A citation whose fact changed or was rejected fails the final check until reviewed (S8)
- [x] A mandatory pack correction blocks signing until acknowledged (F34)
- [x] Invoices come from the case organisation's billing profile with gap-free numbering per issuer; sent invoices never change; corrections are credit notes

## Build notes (2026-10-18, decisions taken while the founder was away)

Decisions (defaults; change by decision record if wrong):
- Signing runs in the request (no job); snapshots are taken at signing. PDFs are drawn with reportlab (LibreOffice PDF is a later request).
- Invoice issuer: the organisation's billing profile for a team case, the named expert's own for a personal case (`app.case_billing_profile`). The rate falls back to the expert profile's hourly rate when the currency matches.
- An invoice PDF is stored (sealed) only when sent, with its number; a draft PDF is drawn on request and never stored. The reference is the instructing party's reference, else the case id. The invoice mail carries the case id, number and amount only (no names); the PDF is not attached (the expert downloads it).
- Time: only the named expert's time is billed; heartbeats count when the tab is visible and input was within 5 minutes; a break over 5 minutes starts a new entry.
- Spot-check: up to 10 random cited facts from the latest report that cites anything; token and code kept as hashes; 7-day expiry; five wrong codes lock the link; crops around the cited words only (never the page), party names and pack identifiers blacked out, watermarked, sealed with a key derived from the link token; every opening or refusal is audited without a person; answers are kept once.
- Web: S18 has no signature canvas (`SignIn` takes the confirm tick; the saved signature comes from slice C). Snapshots view and "add another recipient" form not built yet.

Invoice details (founder approved 2026-10-18; migration 0606, table `time_estimates`):
- Invoice statements come from the jurisdiction pack's optional `invoice.yaml` (`MergedPack.invoice_statements()`; gb-eaw: fees not dependent on the outcome; the fixture pack has none). They are frozen into `invoices.statements` when drafted and print below the total; a pack without any prints nothing.
- Bank details (account name, sort code, account number, IBAN, SWIFT/BIC) are optional, loosely checked columns on `billing_profiles`. They are plain columns, not sealed: billing profiles are not case data and do not use `CaseCrypto`. The expert changes their own (`PUT /me/billing-profile/bank-details`), an organisation owner or admin the organisation's (`/organisations/{org_id}/billing-profile/bank-details`, anyone else 404); every change needs step-up and writes `billing_profile.bank_details_changed` with no values. The draft's issuer snapshot carries them; the PDF's "How to pay" box shows only when filled in. They are never put in any email (`invoice.sent` carries case id, number and amount only).
- Time estimate: append-only `time_estimates` rows per pack workflow step (hours, optional fixed fee), `POST /cases/{id}/time/estimates` (`invoice.create`, audited `time.estimated`); the newest row per step counts. `GET /cases/{id}/time` gives each step's estimate beside its time and `over_estimate` (percent and the pack's instructing-party word) when the time on the estimated steps is over their estimate by more than the pack's `estimate_overrun_percent` (gb-eaw 20, fixture 50; no notice without it). The notice never blocks. The draft freezes `estimate_amount` (fixed fee, else hours at the invoice rate) and the PDF says "Estimate given: X. This invoice: Y." in the invoice's currency, Y being the invoice total including tax.
- The purge order for `time_estimates` was added to 0699 (it runs after 0606 and is F's last definition of `app.case_purge_order()`).

Foundation requests:
- Done: mail templates `invoice.sent` and `spot_check.code` in `app.core.email` (F's own outbox is gone; the invoice email carries no attachment).
- Done: the colleague's routes `GET /v1/spot-checks/{token}` (code in the `X-Spot-Check-Code` header) and `POST /v1/spot-checks/{token}/responses` on the public router (`routes_colleague.py`, mounted in `main.py`). Every outcome is 200 with a `status` (`ok`, `bad_code`, `locked`, `expired`, `unknown`); a wrong code commits so the five-tries lock trips.
- Done: `core/contracts/invoices.case_invoice_rows` (slice H writes `invoices.csv` from it) and `core/contracts/profile.named_expert`.
- Done: section and appendix labels in the pack (`report_sections.yaml` entries are `{key, label}`; `MergedPack.report_appendices()`; the case pack serves `report_appendices`); screens 16 and 17 show them.
- Done: the case frame mounts `useTimeHeartbeat` (`foundation/lib/timeHeartbeat.ts`) for every screen a pack step holds; F's routes name pack screen keys (`report`, `final_check`, `sign_send`, `invoice`).
- Done (0608): a team case builds in the organisation's `policy.required_template_id` template when set (`app.case_report_template`), else the named expert's own; test `test_a_team_case_builds_in_the_organisations_required_template`. Agency and personal invoice numbers run apart without gaps (`test_agency_and_personal_invoice_numbers_run_separately_without_gaps`).
- Still open: page-limit accessors and T-17 prompt context in packs; a CV snapshot source; email attachments; a per-address rate limit on the public spot-check routes (core has no limiter).

## Rules that apply
1, 2, 4, 6, 7, 8, 9, 10, 11 from the root `AGENTS.md`.

## Out of scope
Writing opinions (E), supplementary reports after serving (G, though it reuses `build_report` with `type = supplementary`), Word round-trip import (Phase 2), e-signature for joint statements (I).

## Definition of done
Root checklist, plus: on staging the synthetic case builds, passes the final check once the planted problems are fixed, signs, locks and is marked served, and the hashes verify on download.
