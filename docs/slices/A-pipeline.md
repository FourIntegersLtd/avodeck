# Slice A: Bundle pipeline

Owner: Claude session, branch `slice/a-pipeline` · Phase: MVP · Migrations 0100–0199

## Goal
After upload, the expert gets a numbered, sorted bundle: every page read by OCR, its source and dates identified, facts extracted with links to the exact words, duplicates set aside, gaps and missing documents listed, and a ready-made email to ask the instructing party (the solicitor, under gb-eaw) for what's missing.

## Screens
- `S30-NotThisPatient` Not this patient's record (canvas board 30)
- 07 Preparing your bundle (`S07-Preparing`)
- 08 Your bundle is ready (`S08-BundleReady`)
- 08b Ask the instructing party for what's missing (`S08b-AskSolicitor`; pack label "Ask the solicitor")

## Endpoints (architecture 5.2)
- `POST /v1/cases/{id}/process` → 202 job
- `GET /v1/cases/{id}/bundle/summary`
- `GET /v1/cases/{id}/duplicates`; `PATCH /v1/cases/{id}/pages/{page_id}/duplicate`
- `PATCH /v1/cases/{id}/documents/{doc_id}` (provenance)
- `PATCH /v1/cases/{id}/missing-items/{item_id}`
- `POST /v1/cases/{id}/instructing-party-requests/draft`; `POST /v1/cases/{id}/instructing-party-requests` (returns `mailto:` link in MVP)
- `POST /v1/cases/{id}/documents/append` → pipeline on new pages only
- `GET /v1/cases/{id}/pages/{page_no}/thumbnail` (for duplicate review)

## Tables owned
`ocr_words`, `missing_items`, `record_gaps`, `instructing_party_requests`. Writes the OCR/source/duplicate columns of `pages` and inserts `facts` with status `suggested` (contract owned by foundation).

## Worker job: `process_bundle` / `process_pages`
split (pikepdf) → render 150–200 dpi (pypdfium2) → hashes → text-layer check (born-digital pages: words from pypdfium2, no OCR) → **T-07a** OCR (Azure DI Layout) for the rest → low-confidence flag → `pii.strip` → **T-07b** per-page extraction (Sonnet 5, batch, cached prompt) → `provenance.check` → date normalisation (dateparser, date order from the pack) → duplicates (pHash + MinHash + rapidfuzz, with date/value equality) → record gaps (monthly density per source) → **T-07c** missing documents (references matched to inventory) → embeddings (bge-small) → email via Resend ("Your bundle is ready", no patient details).

## AI tasks (architecture 6.3)
| Task | Model | Output | Check |
|---|---|---|---|
| T-07a | Azure DI `prebuilt-layout` | words, polygons, confidence, handwriting | low confidence → screen 10 queue |
| T-07b | Claude Sonnet 5 (in-region) | `source_type`, `printed_dates[]`, `facts[]`, `references_out[]`, `availability_evidence[]`, `actions_requested_of_clinician[]`, all with `word_ids` | value-in-span; one retry then `needs_human` |
| T-07c | Sonnet 5 (candidate pages chosen by code, nearest by date then page number, until bge-small is in the cell) | `{reference_id, matched_page or null, confidence}` | unmatched, or a page the case lacks → missing list; expert confirms |

## Contracts
- **In:** foundation uploads, jobs, AI kit, `pages` and `facts` contracts.
- **Out:** `facts` (suggested, with `word_ids`, `bbox`, `event_date`, `availability_evidence`), page images and thumbnails, low-confidence queue (B), missing items (08b), appended-page processing (reused by G).

## Acceptance tests (write first)
Using `evals/gb-eaw/clin-neg-breach/general-practice/bundles/synthetic-mrs-a/` (synthetic, 900 pages; planted items listed in `evals/README.md`) with recorded OCR and Claude responses:
- [ ] Every page gets a permanent `bundle_page_no`, 1..N in upload order (the same numbers the instructing party and other experts see); the sorted view groups pages by source then date without renumbering; re-running the job changes nothing (idempotent)
- [ ] Each page's `source_type` matches `evals/gb-eaw/clin-neg-breach/general-practice/expected/sources.json` (≥ 98% on the synthetic bundle)
- [ ] Extracted facts match `evals/gb-eaw/clin-neg-breach/general-practice/expected/facts.json`: precision ≥ 0.95, every fact has `word_ids` and a `bbox` inside the page
- [ ] A recorded response containing a value not in its cited words is rejected and retried once, then marked `needs_human`
- [ ] No identifier from the case name list, and no identifier matched by the pack's recognisers (NHS number and postcode under gb-eaw), appears in any recorded Claude request
- [ ] Under `gb-eaw` (DMY), dates printed as `03/04/2024` are read as 3 April 2024; under the `zz-test` fixture pack (MDY) the same text is read as 4 March
- [ ] The 82 planted duplicates are set aside (not deleted), still numbered, and listed for review; no false duplicates among pages with different dates or values
- [ ] Planted gap (task list stops in January) appears in `record_gaps`
- [ ] Planted missing document (colonoscopy report mentioned on a letter page) appears in `missing_items` with its citing page
- [ ] Low-confidence handwriting pages are queued for screen 10 and never auto-corrected
- [ ] No fact is extracted from text listed in `expected/struck_out.json` (crossed-out entries, wrong-patient blocks)
- [ ] The 148 born-digital pages in the synthetic bundle take their words from the embedded text layer (`text_source = 'embedded'`), make no Azure call, and still produce facts with `word_ids` and boxes; the planted scan with a typed header but a scanned body goes to OCR
- [ ] Job progress reaches 100% with steps split/ocr/sort/duplicates/missing; no patient text in `jobs` or logs
- [ ] Appending 14 pages numbers them 901–914 and processes only those pages
- [ ] The instructing-party email draft lists the open missing items and clearer-copy pages, contains no patient name or NHS number, and returns a valid `mailto:` link; the request is logged
- [ ] Bundle summary returns counts by source, duplicates, unreadable pages, gaps and missing items
- [ ] Screens 07, 08 and 08b pass Playwright + axe and match their canvas boards
- [ ] `ai_calls` records task, model ID, region, prompt version and tokens for every call, with no content

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Test inputs:** synthetic files in `bundles/synthetic-mrs-a/inputs/` (built by `make eval-data`), expected results in `evals/gb-eaw/clin-neg-breach/general-practice/expected/inputs.json` under `wrong_patient`, `foreign_language`, `photographed_page`, `exhibit`, `word_file`, `email`, `zip`.

**Extra tables owned:** `exhibits`. Writes `pages.quarantined`, `pages.superseded_by_page_id`, `pages.language`, `documents.bundle_version`.

**Extra endpoints:** `POST/DELETE /v1/cases/{id}/pages/{page_id}/quarantine`, `POST /v1/cases/{id}/pages/{page_id}/replace`, `GET/POST /v1/cases/{id}/exhibits`, `GET /v1/cases/{id}/bundle/versions`, `POST /v1/cases/{id}/process/cancel`, `POST /v1/cases/{id}/reprocess`.

- [ ] Wrong-patient pages (planted in the synthetic bundle) are suggested for quarantine, never auto-hidden; once confirmed they keep their numbers, show a placeholder, leave AI, search, exports and citations, and their facts are rejected (S5, F25)
- [ ] Quarantined images and text are deleted after the note is sent or 14 days; undo works only before that
- [ ] A clearer copy gets the next number; the old page is superseded and still readable; no fact moves automatically (S6)
- [ ] Images, emails (with attachments), Word and zip files become pages; audio, video and DICOM become exhibits without page numbers (F36)
- [ ] A non-English page is flagged, never machine-translated (F37)
- [ ] Appended records form a new bundle version; the summary says what each version added
- [ ] Cancelling processing stops at a page boundary and keeps finished pages
- [ ] Reprocessing is opt-in per case; verified facts never change; new suggestions carry their model ID

## Rules that apply
1, 2, 4, 5, 6, 7, 9 from the root `AGENTS.md`.

## Out of scope
Showing facts to the expert (B), verifying facts (B), Gmail/Outlook sending (Phase 2), the timeline (D).

## Definition of done
Root checklist, plus: the synthetic bundle processes end to end on staging; `make eval` baseline recorded.
