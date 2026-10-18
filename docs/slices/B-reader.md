# Slice B: Reader

Owner: Claude session, branch `slice/b-reader` · Phase: MVP · Migrations 0200–0299

## Goal
The expert reads every page of the bundle themselves. AI suggestions for a page appear only after they have marked it read. They check each suggestion (Correct, Not right, Change a detail) and type out pages the computer couldn't read. The result is the set of **verified facts** every later slice cites, plus a tamper-evident log proving every page was read.

## Screens
- 09 Reading (`S09-Reader`): page image, "Mark as read", suggestions with highlighted words, keyboard navigation, progress
- 10 Pages we couldn't read (`S10-CouldntRead`): queue, expert transcription (typed or dictated), relevance, clearer-copy request, optional second reading
- Today cards: registers "pages left to read" and "pages to type out" via `NeedsYouItem`

## Endpoints (architecture 5.2)
- `GET /v1/cases/{id}/pages?cursor=&filter=unread|period|source`
- `GET /v1/cases/{id}/pages/{page_no}`: source, period flag, read state, **no facts**
- `GET /v1/cases/{id}/pages/{page_no}/image?zoom=`: streamed, `Cache-Control: private, no-store` (thumbnails are slice A's)
- `POST /v1/cases/{id}/pages/{page_no}/read` `{dwell_seconds}`
- `GET /v1/cases/{id}/pages/{page_no}/facts`: **423 `blind_read`** until read
- `POST /v1/cases/{id}/facts/{fact_id}/verify` `{action: correct|not_right|edit, new_value?, word_ids?, reason?}` (If-Match)
- `POST` / `DELETE /v1/cases/{id}/facts/{fact_id}/flag-point`: marks a checked fact for screen 14, or takes it back (E reads the flags through `core/contracts/fact_flags.py`)
- `GET /v1/cases/{id}/progress`
- `GET /v1/cases/{id}/pages/unreadable`
- `PUT /v1/cases/{id}/pages/{page_no}/transcription` `{text, relevance?}` → 202 job (re-extraction through `core/contracts/pipeline.py` `extract_page_text`, which A implements)
- `GET /v1/cases/{id}/pages/{page_no}/second-reading`
- `PATCH /v1/cases/{id}/pages/{page_no}` `{relevance?, needs_clearer_copy?}` (setting a page aside as a copy is A's `PATCH …/pages/{page_id}/duplicate`)

## Tables owned
`page_reads` (append-only, hash-chained), `fact_verifications` (append-only), `fact_flags` (fact_id, flagged_for: points, at).

Writes on contract tables: `facts.status` and `facts.value` / `fields` on edit (the original stays in `fact_verifications.old_value`); `pages.relevance`, `pages.transcribed_by_expert_text`, `pages.needs_clearer_copy`. Reads `pages`, `facts`, `ocr_words` (A).

## Worker job
`process_pages` in **transcription mode** (code owned by A, called through `core/contracts/pipeline.py`):
1. Tokenise the expert's text into `ocr_words` rows with `source = 'expert'`.
2. Run `pii.strip`, then T-07b, then `provenance.check` against those words.
3. Store the new facts as `suggested`.

The expert's text is authoritative. Machine words for that page are kept but no longer used for extraction.

## AI tasks (architecture 6.3)
| Task | Model | Input | Output | Check |
|---|---|---|---|---|
| T-10 Second reading | Sonnet 5 vision, London in-region | Page image with identifier regions masked (boxes from M9 over OCR words) | `{lines[{text, confidence}]}` | Only after the expert has transcribed, or when they press "Ask for a second reading"; shown side by side, never merged into the expert's text |
| T-07b (re-run) | Sonnet 5 | Expert transcription tokens | As slice A | Value-in-span against the expert's words |
| T-DICT | Azure Speech (foundation kit) | Audio | Transcript | Expert edits |

## Contracts
- **In:** `pages`, `facts` (suggested) and `ocr_words` from A. Until A lands, use the stub loader `tests/fixtures/stub_facts.py`, built from `evals/gb-eaw/clin-neg-breach/general-practice/expected/facts.json`.
- **Out:**
  - `core/contracts/facts.py: citable_facts(case_id)` returns verified facts whose page has a `page_reads` row. E, F, G and I may cite only these.
  - The page-read log (F's methodology appendix and H's export).
  - Fact flags (E).
  - Reading progress (Today).

## Acceptance tests (write first)
- [x] `GET …/pages/118/facts` returns 423 with code `blind_read` before `POST …/pages/118/read`, and 200 after. `GET …/pages/118` never contains facts.
- [x] A different expert gets 404 on every route above.
- [x] Each read appends one `page_reads` row with `prev_hash` and `hash`. The chain verifies, and altering any row breaks verification. UPDATE and DELETE on `page_reads` fail at database level.
- [x] Marking a page read twice keeps the first read time (`at`) and adds the second dwell to the log. Reads are never removed.
- [x] `verify` with `correct` sets `facts.status = verified` and appends to `fact_verifications`.
- [x] `verify` with `edit` stores old and new values. The new value must still appear in the cited words, or the expert must re-select words; otherwise 422.
- [x] `verify` with `not_right` sets `rejected` and asks for an optional reason.
- [x] `verify` on a fact whose page has not been read returns 423.
- [x] `citable_facts()` excludes suggested, rejected and unread-page facts. A core test calls it from a stub report build.
- [x] Suggestions return their `bbox` and `word_ids`. The screen highlights exactly those words (Playwright screenshot check on page 118).
- [x] Facts on duplicate pages are shown on the original, not twice.
- [x] Unreadable queue lists the planted low-confidence handwriting pages (`expected/unreadable.json`) with a done count.
- [x] Saving a transcription queues re-extraction. Resulting facts cite `source = 'expert'` words and start as `suggested`. The page counts as read.
- [ ] (deferred, needs foundation vision) T-10 is refused (409) before a transcription exists unless `?on_request=true`. The recorded vision request contains no unmasked identifier region (checked against the M9 boxes).
- [ ] (deferred) T-10 output is never written into `transcribed_by_expert_text`.
- [x] `needs_clearer_copy = true` makes the page appear in slice A's instructing-party request draft.
- [x] Progress returns pages read / total and a time estimate from the expert's own median seconds per page by source (default 20 s before there's history).
- [x] Keyboard: `J`/`K` move page, `R` marks read, `1`/`2`/`3` act on the focused suggestion. All actions are also reachable by 52 px buttons.
- [x] No page text, OCR words or transcription appear in logs, job rows or Sentry events.
- [x] Screens 09 and 10 pass Playwright + axe and match their canvas boards.

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Test inputs:** synthetic files in `bundles/synthetic-mrs-a/inputs/` (built by `make eval-data`), expected results in `evals/gb-eaw/clin-neg-breach/general-practice/expected/inputs.json` under `rotation`.

**Extra endpoints:** `POST /v1/cases/{id}/pages/{page_id}/rotate`, `POST /v1/cases/{id}/facts/{fact_id}/move`.

- [x] Only the named expert's `page_reads` unlock citing; an assistant's reads are recorded but don't count (permissions `page.read`)
- [x] Only the named expert can verify facts (403 for anyone else)
- [x] Rotation re-runs OCR, keeps the page number, keeps verified facts and re-maps their boxes or flags "Please check"
- [x] Moving a verified fact to a clearer copy keeps its history and bumps its `version`
- [x] Editing a fact bumps `facts.version`; citations then show "changed since cited" (S8)
- [x] Facts on a duplicate page appear only on the original

## Rules that apply
1, 2, 3, 4, 6, 7, 9 from the root `AGENTS.md`.

## Out of scope
OCR and extraction (A), the timeline and links (D), points against (E), Gmail sending (Phase 2).

## Definition of done
Root checklist, plus: on staging, a test expert reads the synthetic bundle and verifies facts, and the page-read log exports as CSV with a valid hash chain.
