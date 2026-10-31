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
- `GET /v1/cases/{id}/pages/{page_no}/words`: the page's words with boxes (null for typed words), for re-citing a changed fact, and `reading` (corrections in place) to prefill the type-out on S10; **423 `blind_read`** until read, `private, no-store`
- `POST /v1/cases/{id}/facts/{fact_id}/verify` `{action: correct|not_right|edit, new_value?, word_ids?, reason?, fields?, event_date?, corrected_words?}` (If-Match; `fields` checked against the pack kind: 422 `invalid_fields`; `corrected_words {word_ids, text}`: the expert's reading of misread cited words, decision 0018; facts carry `cites_correction`)
- `POST` / `DELETE /v1/cases/{id}/facts/{fact_id}/flag-point`: marks a checked fact for screen 14, or takes it back (E reads the flags through `core/contracts/fact_flags.py`)
- `GET /v1/cases/{id}/progress`
- `GET /v1/cases/{id}/pages/{n}` also carries `period_start` / `period_end` (the case's period of interest, read from `cases`), so the S09 banner can say "(Feb 2024 to Aug 2024)"
- `GET /v1/cases/{id}/pages/unreadable` (each page also says `is_copy` and `has_clearer_copy`; `pages/{n}/facts` rows carry `related`: other suggestions on the same page and day, shown as "the record found with it") The facts list also says `reassigned` (true when a fact on the page is suggested again because the named expert changed; screen 09 then explains it, nothing lost).
- `PUT /v1/cases/{id}/pages/{page_no}/transcription` `{text, relevance?}` → 202 job (re-extraction through `core/contracts/pipeline.py` `extract_page_text`, which A implements)
- `GET /v1/cases/{id}/pages/{page_no}/second-reading`
- Reading by transcript (S09b, ADR 0028, migration 0215): `GET /v1/cases/{id}/transcript/entries?offset&limit` (`{status, total, entries, picture_pages, unread_pages}`, no facts; `unread_pages` are pages the computer could not read, read by opening the picture; a failed entries job is not re-queued by polling), `POST …/transcript/retry` (202 `{status}`, `page.read`; the reader's own "Try again"), `POST …/transcript/seen` (`{entry_id, dwell_ms}` or `{page_no, picture_opened}` returns `{pages_read}`; the server decides when a page is read, 1.5 s per entry, and logs it in `page_reads` with `via = 'transcript'`), `POST …/transcript/download` (202 `{job_id}`, step-up, job `export_transcript`) and `GET …/transcript/download/{job_id}` (the Word file once)
- `PATCH /v1/cases/{id}/pages/{page_no}` `{relevance? (relevant | background | nothing), needs_clearer_copy?}` (setting a page aside as a copy is A's `PATCH …/pages/{page_id}/duplicate`)

## Tables owned
`page_reads` (append-only, hash-chained; `via` page|transcript), `transcript_views` (append-only; 0215, purge splice 1016), `fact_verifications` (append-only), `fact_flags` (fact_id, flagged_for: points, at), `word_corrections` (the expert's reading of misread words; page-owned, no case_id; decision 0018, migration 0209), `page_turns` (how a page is turned for reading, display only; page-owned, no case_id; migration 0213), `page_notes` (the person's own bookmarks and private notes; page-owned, no case_id; migration 0214).

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
  - The page-read log (F's methodology appendix and H's export): `core/contracts/reading.py` `page_read_log(conn, case_id)` and `verification_counts(conn, case_id)`.
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

**Extra endpoints:** `POST /v1/cases/{id}/pages/{page_id}/rotate` (kept for clients; screen 09 no longer uses it), `PUT /v1/cases/{id}/pages/{page_no}/turn`, `GET …/pages/{page_no}/image?size=small`, `POST /v1/cases/{id}/facts/{fact_id}/move`.

**Page viewer (approved 2026-10-23, PR1):**
- [x] Turn left, Turn right, Upside down and Undo turn (keys T, Shift+T, U) turn the page at once; the turn is saved in `page_turns` (display only, nothing is read again); the picture, highlights and word picker turn as one, so they stay over the same words at 90, 180, 270 and after undo
- [x] Fit width, Whole page, zoom buttons, Ctrl and the wheel, pinch, and drag to move about; the view stays as left on every page for the visit (held in memory; no browser storage)
- [x] Go to page, and a page strip of every page with read marks: drawn a screenful at a time (4,000 pages), small pictures only for what is drawn, works from the keyboard
- [x] Make it clearer: brightness and contrast change only how the picture is shown

**Page viewer (approved 2026-10-23, PR2):**
- [x] Find words in the bundle (`POST …/search`): only the words read from the pages, with the expert's corrections; never suggestions or facts, so searching is not reading and the blind read holds; answers page numbers and places, hits marked on the page
- [x] Two pages side by side: the clearer copy, else the next page, or any page number
- [x] Full screen (button, F, Escape); view settings held in memory only
- [x] The person's own bookmarks and private notes (`page_notes`, migration 0214): RLS per person, sealed, not reading, never sent to AI; goes with its page on case deletion (no case_id, so no purge-list entry)

- [x] Only the named expert's `page_reads` unlock citing; an assistant's reads are recorded but don't count (permissions `page.read`)
- [x] Only the named expert can verify facts (403 for anyone else)
- [x] Rotation re-runs OCR, keeps the page number, keeps verified facts and re-maps their boxes or flags "Please check"
- [x] Moving a verified fact to a clearer copy keeps its history and bumps its `version`
- [x] Editing a fact bumps `facts.version`; citations then show "changed since cited" (S8)
- [x] Facts on a duplicate page appear only on the original
- [x] Next and `J` stop at the last page (the next page comes from the page list)
- [x] The fact card offers, by button and key, only what the state machine allows (Change only on suggestions; Correct on a checked fact only when it says "Please check")
- [x] "Change a detail" updates `fields`, `event_date` and the box from the chosen words, and logs the real old and new details
- [x] "Please check" (`recheck`) flags show as "Please check", never as "In Points against"; "Take out" removes only the points flag; checking the fact again removes the recheck flag
- [x] A checked fact can still be changed ("Change a detail", "Set date on timeline"); 409 `served_use_supplementary` when a served report cites it, with a link to the supplementary report; an opinion citing it shows the citation changed since cited
- [x] "Move to the clearer copy" on a fact whose page was replaced: choose the words on the copy (`POST …/facts/{id}/move`); the copy must be read first
- [x] S10 starts the type-out from the page's current reading; Save says why it cannot save yet instead of greying out
- [x] Kinds and details use the pack's words (`kindWord`, `fieldWord`)
- [x] "Change a detail" lets the expert choose the cited words over the page (`GET …/words`, toggles by mouse or keyboard); a "Please check" fact saves only with words chosen (422 `words_needed` otherwise)
- [x] Changed `fields` must be fields of the fact's kind in the case pack, with values of their types (422 `invalid_fields`), under the launch and fixture packs
- [x] A re-read re-maps a checked fact only when its words match under rule 2's normaliser ("1.5 mg" is not "15 mg"); each re-map appends a `remapped` verification row; typed words give no box

## Rules that apply
1, 2, 3, 4, 6, 7, 9 from the root `AGENTS.md`.

## Out of scope
OCR and extraction (A), the timeline and links (D), points against (E), Gmail sending (Phase 2).

## Definition of done
Root checklist, plus: on staging, a test expert reads the synthetic bundle and verifies facts, and the page-read log exports as CSV with a valid hash chain.
