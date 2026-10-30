# 0028 The records transcript, and reading by transcript

Date: 2026-10-30 · Status: accepted · Decided by: the founder (plan "lazy-mapping-waterfall", item 5) · Builds on: rules 1, 2, 3 and 6, ADR 0026

**Problem.** An expert reads 1,000 to 4,000 pages, many of them handwritten or printed from a records system in a way that is hard to follow page by page. They also read the same story many times across pages (a consultation split over two pages, results spread through the bundle). A page-by-page reader is faithful to the bundle but slow. The experts asked for the bundle's own words, laid out entry by entry in date order, that they can read like a chronology.

**Decision.**
- **The transcript is the page's own words, re-laid out.** Avodeck finds where each entry starts and ends on a page (T-07b `entries[]`: kind, first word, last word, author role, specialty) and the reader draws those words, in date order, grouped by entry. No AI prose is added anywhere (rule 1). The model writes no words of its own: it names two word IDs and pack keys. The server checks both ends are words on the page, first is not after last, the entries do not overlap and the kind, role and specialty are in the pack; any that fail are dropped and counted, never logged with text (rule 2, rule 6).
- **Reading entries in transcript mode counts as reading the page, and the server decides.** `page_reads` gains `via` (`page` or `transcript`). The read is recorded only when the server has seen that every entry on the page has been on screen for at least the pack's read dwell (the reader reports how long each was shown; the server holds the rule and the time, rule 3). Pages the computer could not read count only when the picture has been opened.
- **Administrative entries fold, and still count as seen.** A kind marked `transcript: exclude` in the pack (appointment booking, registration, administrative task, repeat-prescription request, appointment letter at launch) shows as a folded line: its kind label and the first line of its words. It counts as seen by dwell on the folded line. We chose this over hiding it entirely (the expert would never have been shown it, so reading it could not be claimed) and over opening every one (it buries the clinical entries the transcript exists to bring forward). The expert can unfold any line.
- **The Word download covers only pages read by the named expert and is an export** (step-up, audit, rule 10). **There is no Word file before reading.** The file holds the same words in the same layout, so it is subject to the same rule: nothing is given out for a page not yet read.
- **Reading prompts are an index, not a judgement.** `reading_prompts.yaml` and `result_groups.yaml` are pack lists of words. The reader shows where those words appear in the record, with the pack's label. The labels are neutral ("Pain", "Injury"), a prompt never says a word is important, and no prompt reaches a conclusion (intended-purpose guardrails 3 and 4; this record is the regulatory check guardrail 4 asks for).

**Why the model finds entries and does not write a chronology.** A model-written chronology is prose, so it would break rule 1 and could not be checked against the words. Two word IDs can be checked by code.

**Data.** `app.record_entries` (migration 0121) holds one row per entry: page, case, kind, first and last word number, author role, specialty, event date and its basis, extraction version. It holds no text. `event_date` comes from the dates printed inside the entry's words, read by code in the pack's date order (a date that reads two ways is "Please check", never guessed). Slice B reads it through `core/contracts/record_entries.py`.

**Cases read before this change.** Their pages have no entries. The job `extract_entries` asks T-07b again for pages without entries, writes only entries (facts and notes are untouched), and does nothing when they exist. `entries_ready` tells the reader when to draw the transcript; until then it offers the page-by-page reader.

**Consequences.**
- T-07b output grows by a short list of IDs per page. Cost rises slightly; the prompt version changes, so `make eval` must pass.
- Packs gain four files (`entries.yaml`, `specialties.yaml`, `result_groups.yaml`, `reading_prompts.yaml`); a pack with no `entries.yaml` has no transcript and the page reader is used.
- Slice B builds the transcript screen, the `via` read and the Word export; this slice delivers the data and the contract.

**Tests.** `api/tests/unit/test_pipeline_entries.py`, `api/tests/unit/test_foundation_packs_entries.py`, `api/tests/repository/test_pipeline_record_entries.py`, `api/tests/unit/test_pipeline_eval_entries.py`.
