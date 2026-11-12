# Audit 16: document provenance and output integrity

Date: 2026-11-12. Branch: `docs/audit-16-document-provenance` (stacked on audit 15).

Scope: can every output (fact, timeline entry, citation, report, transcript, trial pack, case export) be traced to the exact source page and words, and can anything change it without being noticed? Audits 11 (access), 12 (storage), 14 (AI) and 15 (background jobs) are cross-referenced, not repeated.

Each claim is marked **T** (a test ran and showed it), **R** (read in code) or **U** (unverified).

## Summary

The provenance chain is sound in design: a hashed source file, page numbers that never change, word ids per page, facts that cite words, a hash-chained record of every human check, citations pinned to a fact version, and locked report versions with per-format hashes.

Before this branch the chain had gaps at almost every link. Most rules lived only in application code, so a database write through the application's own role could break them without trace. Several edits left no history. Several outputs were sent without comparing them to their recorded hash. A page could drop out of the reading and the report without a human deciding so.

21 findings were raised (PV-01 to PV-20, with PV-11 split in two; PV-20 came from the independent review). 19 are fixed on this branch, each proved by a test that failed before the fix. PV-13 and PV-01 are fixed in part, with the rest recorded as policy. PV-11b is deferred with a recorded trigger. One suspicion (PV-19b) proved to be a control that already held. Nothing was found that let one case's records reach another case.

| Severity | Count | Fixed here |
|---|---|---|
| High | 6 | 6 |
| Medium | 11 | 9, and 2 in part |
| Low–medium | 1 | 1 |
| Low | 3 | 2, 1 deferred |

## How this was tested

- Four read-only sweeps mapped the model from code and migrations. Nothing was assumed about schema, numbering or output format.
- For each suspected weakness the owning slice wrote a test that asserts the safe outcome, ran it, and confirmed it failed for the right reason before changing code. A test that passed before any change is kept as a control and the suspicion dropped.
- Tests ran against a spare local database (never the app or founder databases), with every migration applied.
- Synthetic records only. No patient text is in the tests, the logs or this document.

Test files, all in `api/tests/security/`:

| File | Tests | Result |
|---|---|---|
| `test_foundation_provenance_integrity.py` | 47 | green |
| `test_pipeline_provenance_integrity.py` | 16 | green |
| `test_reader_provenance_integrity.py`, `test_reader_second_reading_ocr_record.py` | 30 + PV-09 and export cases, 3 | green |
| `test_report_provenance_integrity.py` | 45 (two pack sets) | green |
| `test_trial_provenance_integrity.py` | 12 | green |
| `test_close_provenance_integrity.py` | 8 | green |
| `test_after_serving_provenance_integrity.py` | 32 | green |

## 1. Verified provenance model

One chain per output. Each link names the table and column that holds it.

**Source file.** `documents.sha256` is checked when an upload is finalised and when a download ends (`api/app/core/uploads.py`, `api/app/core/routes/uploads.py`). Parts are sealed per path. R.

**Page.** `pages.bundle_page_no` is unique per bundle and never renumbered (`pages_guard`). The sorted view is read-only; sorting never moves a number. T.

**Words.** `ocr_words (page_id, word_id)`; ids are `p{page}w{n}`, or `e{n}` for text the expert typed. On a re-read the words are replaced and checked facts are re-mapped. R.

**Fact.** `facts.page_id`, `word_ids`, `bbox`, `event_date`, `date_basis`. The value must be found in the cited words (`api/app/core/ai/provenance.py`). T.

**Human check.** `fact_verifications` and `page_reads` are append-only and hash-chained. Only a `verified` fact can be cited. T.

**Timeline.** A computed view over facts. It has no table of its own, so it cannot drift from the facts. R.

**Report.** `citations` carry `fact_version` and a sealed snapshot. `report_versions_guard` locks a signed version. `report_snapshots` holds one hash per format. T.

**Trial.** `court_bundles.sha256`; `concordance` maps a trial page to a bundle page; `trial_packs.sha256`; `offline_packages.sha256`. T.

**Audit.** `app.audit_log`, one global hash chain. T.

Per output type, the path back to source is:

| Output | Path to source |
|---|---|
| Timeline entry | fact → page → document hash |
| Citation in a report | citation (`fact_version`) → fact → `word_ids` → page number → document hash |
| Signed report file | `report_snapshots.sha256` per format → `report_versions` (locked) → citations |
| Transcript export | `transcript_files` hash → pages read, with set-aside pages listed by number and reason |
| Trial pack, offline package | recorded hash → `sources` block (court bundle hash, report version hash, pack versions) |
| Concordance CSV | response headers name the court bundle and its hash, and the CSV's own hash |
| Case export | archive hash in the audit log → `manifest.json` (report status, version hash, document hashes) → `hashes.csv` |

## 2. Record manipulation

What stops a record being changed, and what did not.

- **PV-06.** The application role could update `documents.sha256`, `storage_path`, and a page's `image_path` and `document_id`. Row-level security allowed it and no trigger refused it. A changed hash would then "verify" a swapped file. Fixed: `documents_guard` freezes the hash, bundle, kind and filename; `pages_guard` freezes the document link for everyone and the image path, text source and confidence for the application role. T.
- **PV-07.** The pipeline never re-checked the source file's hash after upload, and page images had no hash. Fixed: the whole-file hash and part count are checked when the pipeline fetches a file, and each page has `content_sha256`, checked when it is read back. A mismatch marks the document or page `altered`. T. Page images at fixed storage keys remain as audit 12 section 5 describes.
- **PV-01.** The rule that a fact's words are on its own page was enforced only in application code. Fixed: a database check at commit refuses, for both application roles, any fact whose `word_ids` are not words of its own page. The value-in-words check stays in the application because the value is sealed. T.
- **PV-02.** The value and words of a checked fact could change with no record. Fixed: such a change is refused unless a `fact_verifications` row in the same transaction carries the new value. T. A pipeline date repair on a checked, undated fact keeps the fact's own words, adds the date's, and now writes a `date_repair` log row with before and after. T.
- Locked report versions: paragraphs, citations, snapshots and builds of a locked version could be changed through definer functions. Fixed: refused for both application roles; the case purge still works. T.

## 3. Chronology and classification

- **PV-03.** A date-only edit kept no record of the old date, and a date typed by the expert was labelled as printed on the page. Fixed: the check log holds old and new date and basis, inside the row hash; an expert's date is labelled `expert_entered`. T.
- **PV-04.** Setting a page aside (quarantine) and undoing it moved facts between rejected and verified with no check-log row. Fixed: both write rows. T.
- **PV-05.** A suggestion refused by the system and one rejected by the expert looked the same, and every refusal collapsed to one label. Fixed: `facts.withdrawn_reason` and `page_analysis.refusal_codes`. T.
- **PV-15 (chronology part).** The chronology appendix dropped undated facts without saying so. Fixed: undated entries are kept and each date shows its basis. T.
- Source-type and relevance changes had no history (PV-12). Fixed. T.
- Ambiguous day and month order, two-digit years, two dates on one page and retrospective entries are handled by pack date rules and `date_basis`; no new weakness found. Controls pass. T.

## 4. Missing and duplicate pages

- **PV-08.** There was no stored original page index; it was derived by assuming a document's pages are contiguous, and new numbers came from `max()+1`. Fixed: `pages.source_page_index` (immutable) and `bundles.page_high_water`, so a number is never reused. T.
- **PV-09.** A page only *suggested* as a duplicate was left out of AI reading, the transcript and the counts before any human confirmed it. Fixed everywhere: only a confirmed duplicate is set aside, through one shared rule (`api/app/core/contracts/set_aside.py`). T. Cost note: suggested duplicates are now read by the AI, which costs more on bundles with many copies.
- **PV-10.** A blank page and a page whose text reading failed looked the same, and a page with very few words and no facts got no flag. Fixed: `ocr_failed` is distinct from `no_words`, and `few_words` is flagged. T.
- **PV-11a.** Nothing compared the page count a file declares with the pages found. Fixed: `documents.pages_declared`, with shortfalls shown in the missing-items list and disclosed in the report's method section. T.
- **PV-11b.** Breaks in printed page-number sequences are not detected. Deferred (`docs/decisions/deferred.md`), with its trigger.

## 5. Human review and audit trail

- **PV-12.** These edits left no history and no audit row: typed transcription, relevance, rotation, source type, fact flags (hard delete) and report section text. Fixed: append-only history tables (`page_transcriptions`, `page_changes`, `fact_flag_history`, `report_section_versions`, `concordance_history`) and audit rows that carry no page text. T.
- **PV-13.** The audit chain was never verified at run time, and a row's time could be back-dated on insert. Fixed: the time is forced to the clock for application roles, update and delete are refused, and a worker sweep verifies the audit chain and each case's chains (`verify_audit_chain`, `verify_case_chains`). T. Still open as policy: the chain is unkeyed and not anchored outside the database, and rows hold no before and after values.
- **PV-18.** Which code and model produced a row could not always be told. Fixed where found: `ai_calls.job_id` and `api_version`; every OCR run in pipeline, reader, trial and after-serving records a call. T. Remaining model-id columns are listed under Unverified.
- Blind read, the named-expert rule and step-up were re-checked as controls and hold. T.

## 6. Output and export integrity

- **PV-14.** A citation to a page later set aside, or unreadable with nothing typed, was still rendered, and signing checked citations only in opinions, not section text. Fixed: such citations are not rendered, the final check names the page, and signing re-checks. T.
- **PV-15.** The final check did not gate unreadable pages, failed text reading or an unfinished pipeline. Fixed: all block signing. Drafts still build but are marked incomplete. T.
- **PV-16.** Trial pack and offline package downloads did not compare the stored file with its recorded hash; two pages could share a trial page number; a changed trial page left no history; the concordance CSV named no court bundle. Fixed: 409 `hash_mismatch`, a unique index, append-only history with an audit row, and response headers. T.
- **PV-17.** In the case export the recorded hash for a signed PDF was the combined version hash, so a genuine file read as a mismatch; the archive had no hash; drafts and withdrawn versions carried no status. Fixed: per-format hashes, archive hash recorded and checked at download, and a manifest with status, pack versions, code version and document hashes. T. The export still leaves out checked facts and citations, by design (policy item 3).
- **PV-19.** Signing a supplementary report or a set of answers lacked the protections of the main report's signing. Fixed: both paths lock their rows, refuse when a cited fact no longer stands (409 `citation_problems`), refuse when the draft changed after it was built (409 `draft_changed`), and freeze snapshots. T. The download already verified its hash (control). The final-check gate is not applied to these two paths (policy item 6).
- Transcript export: no recorded hash, and set-aside pages appeared only as a count. Fixed: hash recorded and checked, pages listed by number and reason. T.

## 7. Synthetic-record results

| Condition | Before | Now |
|---|---|---|
| Fact citing words of another page | silently accepted at the database | refused (T) |
| Fact citing words of another case | refused by row-level security | refused (T, control) |
| Value of a checked fact changed directly | silently accepted | refused without a check-log row (T) |
| Date changed on a checked fact | accepted, old date lost | old and new kept (T) |
| Date typed by the expert | labelled as printed | labelled `expert_entered` (T) |
| Ambiguous day and month, two-digit year | handled by pack date rules | unchanged (T, control) |
| Undated fact in the chronology | silently dropped | kept and marked (T) |
| Duplicate page, suggested only | silently set aside | read and counted until confirmed (T) |
| Duplicate page, confirmed | set aside | set aside and listed in outputs (T) |
| Missing pages against the declared count | silently accepted | flagged and disclosed (T) |
| Skipped printed page number | silently accepted | silently accepted (deferred, PV-11b) |
| Blank page against failed reading | indistinguishable | distinct (T) |
| Conflicting facts on one page | both shown to the expert | unchanged (T, control) |
| Source file hash changed in the database | silently accepted | refused (T) |
| Source file bytes changed in storage | not noticed by the pipeline | detected, marked `altered` (T) |
| Page content changed after reading | not noticed | detected at read-back (T) |
| Stored trial pack, offline package, transcript or export swapped | sent | refused, 409 (T) |
| Audit row back-dated, changed or deleted | accepted | refused (T) |
| Row of a locked report changed | accepted through definer paths | refused (T) |

## 8. Findings register

| Id | Severity | Finding | Evidence | Verified | Fix | Owner (status) |
|---|---|---|---|---|---|---|
| PV-01 | Medium | Fact provenance enforced only in application code | `0219` `facts_guard`; `test_pv01_*` | T | Deferred database check, `0226` | Reader (fixed for a changed fact and a single-case re-read; open on a bundle shared between cases, policy item 7) |
| PV-02 | High | Checked fact's value and words changeable with no log row | `test_pv02_*`; `pipeline/repo.py` `set_fact_event_date` | T | `0226` (status, value, words and dates; log row must carry old and new values); date-repair, quarantine and undo logging | Reader (fixed after review); Pipeline (fixed) |
| PV-03 | Medium | Date edits unrecorded; expert date labelled printed | `test_pv03a_*`, `test_pv03b_*` | T | `0227`; `expert_entered` | Reader (fixed) |
| PV-04 | Medium | Quarantine and undo unlogged | `test_pipeline_provenance_integrity.py` | T | Check-log rows | Pipeline (fixed) |
| PV-05 | Low | System and expert rejection indistinguishable | same file | T | `withdrawn_reason`, `refusal_codes` | Pipeline (fixed) |
| PV-06 | High | Application role could change document hash and page file links | `test_foundation_provenance_integrity.py` | T | `0074` guards | Foundation (fixed) |
| PV-07 | Medium | Source hash never re-checked; pages unhashed | pipeline file | T | Hash and part-count check; `content_sha256` | Pipeline (fixed) |
| PV-08 | Low–medium | No stored source page index; numbers reusable | pipeline file | T | `source_page_index`, `page_high_water` | Pipeline (fixed) |
| PV-09 | Medium | Suggested duplicate set aside before confirmation | `test_PV09_*`, `test_pv09_*`, foundation follow-up tests | T | Confirmed-only rule, shared helper | Pipeline, Reader, Foundation (fixed) |
| PV-10 | Medium | Blank and failed-reading pages alike; sparse pages unflagged | pipeline file | T | `ocr_failed`, `few_words` | Pipeline (fixed) |
| PV-11a | Medium | Declared page count never compared | pipeline file | T | `pages_declared`, shortfalls | Pipeline (fixed) |
| PV-11b | Low | Printed page-number breaks undetected | read | R | Deferred | Pipeline (deferred) |
| PV-12 | Medium | Edits with no history or audit row | `test_pv12_*` (reader, report), `test_pv16d_*` | T | History tables, audit rows; flag history scoped to its case; concordance history refuses change by trigger | Reader, Report, Trial (fixed after review) |
| PV-13 | Medium | Audit chain unverified, back-datable, unkeyed | foundation file | T | `0074`, `9009`, sweep | Foundation (fixed in part; policy items 1, 2) |
| PV-14 | High | Citations to set-aside pages rendered; section citations unchecked at signing | `test_pv14a_*`, `test_pv14b_*` | T | Render filter, final check, sign re-check | Report, Foundation (fixed) |
| PV-15 | High | Final check missed unreadable, failed and unfinished pages; chronology dropped undated facts | `test_pv15_*` | T | Blocking checks; chronology | Report (fixed) |
| PV-16 | Medium | Trial downloads unverified; duplicate trial pages; no concordance history | `test_pv16a`–`e_*` | T | `0904`, hash checks, headers | Trial (fixed) |
| PV-17 | Medium | Case export hashes wrong or absent; no status on drafts | `test_pv17a`–`c_*` | T | Per-format hashes, archive hash, manifest | Close (fixed; 17d policy) |
| PV-18 | Low | Job, code version and OCR model not recorded on outputs | `test_pv18_*`, `test_PV18_*` | T | `ai_calls.job_id`, `api_version`, `record_call` | Foundation, Pipeline, Reader, Trial (fixed); After serving (see PV-19 row) |
| PV-19a | High | Supplementary and answers signing lacked the citation check, frozen snapshots and a row lock | `test_pv19a_*` | T | `after_serving/signing.py`: row lock, 409 `citation_problems`, 409 `draft_changed`, snapshots | After serving (fixed; final-check gate is policy item 6) |
| PV-19b | Info | Supplementary and answers download verifies the stored file's hash | `test_pv19b_*` | T | None needed (control) | After serving (holds) |
| PV-20 | High | Found in review. The append-only and locked-row triggers skipped their check when a session setting (`app.purging_case_id`) was set, and any database role can set it, so a restricted login could delete signed or hash-chained rows | `test_pv20_*` (foundation; 9 red, 1 control) | T | `0075` marker row only the purge function can write; `9010` re-points every guard to `app.case_is_purging` | Foundation (fixed) |

## Policy items

Not code defects. Each needs a decision.

1. **Anchor the audit chain outside the database.** Someone with full database access could rewrite the whole chain consistently. A periodic signed checkpoint of the head hashes, with the key held outside the database and written to an object-locked bucket, would close this.
2. **Before and after values in audit rows.** Audit rows say what happened and to what, not the old and new values. The history tables added here cover the edits that matter for provenance.
3. **Provenance in the case export (PV-17d).** The export contains reports, method, reads and invoice by design. A file listing each cited fact's page, word ids, checker and time would need a new shared contract.
4. **Printed page-number breaks (PV-11b).** Deferred with a trigger.
5. **Chronology entries are not stored as citations.** They are rendered from checked facts at build time and frozen in the snapshot, but have no `citations` row.

6. **Final check before signing a supplementary report or answers.** The main report cannot be signed while its final check has blocking items. The supplementary path has no final check, and an existing test signs one without it. Whether these shorter documents need a final check of their own is a product decision.
7. **Re-read on a bundle shared between cases (PV-01 residue).** A re-read replaces a page's words. The worker's row-level rules hide the other case's checked facts, so the re-map never sees them and a checked fact in the other case could be left citing words that no longer exist. Closing it needs a security-definer re-map over every case on the bundle. Recorded as a strict expected-failure test (`test_reader_provenance_integrity.py`).

## Unverified

- Model id columns on `points`, `relevant_facts`, `witness_claims`, `fact_question_links`, `lint_issues`, `instruction_extractions` and `prefill_suggestions`, and OCR call recording in case setup (`setup/jobs.py`). Read, not tested or changed. U.
- Two trial rate-limit tests (`test_trial_ai_agenda.py`, loop limits) failed on the loaded test machine; the routes are untouched by this branch. To be re-run on a quiet machine or by CI. U.
- `test_control_the_same_insert_succeeds_for_the_legitimate_owner[jobs]` fails with 42501 from audit 15's migration `0068` (commit `fc4ba869`), not from this branch. U.
- Whether the shared-bundle residue (policy item 7) occurs in practice depends on how often a bundle is shared between cases. U.
- The full API suite was not run locally; CI is the gate.

## Cross-references

Recorded in earlier audits and not repeated: document delete returning 404 (11 NEW-4, 12 D6a), damaged files losing pages (12 V4d), spot-check send without step-up (11 L5b), hidden characters in provenance (14 R7–R9), page images at fixed storage keys (12 section 5).

## Recommendations, in order

1. Decide policy item 6 (a final check for supplementary reports and answers).
2. Decide policy item 1 (anchoring). It is the only remaining way to alter history without trace.
3. Add the model-id columns listed under Unverified, one slice at a time.
4. Decide policy item 3 if exported cases must stand alone as a provenance record.
5. Build PV-11b when its trigger in `deferred.md` is met.

## Existing tests changed

Fixes changed behaviour that a few existing tests had pinned. In each case only the seed changed, so the test still asserts what it did:

- Pipeline, reader and foundation tests that seeded a set-aside copy now seed a *confirmed* copy (PV-09).
- The tenant-isolation export seed now records the archive hash, as the real job does (PV-17b).
- A contract test now seeds the word its fact cites (PV-01).
- `test_any_other_change_to_a_fact_still_raises_its_version` now makes its change as the named expert with a check-log row, as real code must (PV-02). Assertions unchanged; a new test proves the bare change is refused.

- The tenant-isolation catalogue (`test_foundation_tenant_isolation_db_layer.py`) lists tables with no tenant key in `EXPECTED_UNKEYED`. `chain_checks` and `case_purge_marks` were added (both are written only by definer functions and no app role can read them). This is a change to an existing test's list, not only a seed, so it needs a person's approval.
- The transcript download seed and the isolation world now record the hash row and a history row the real jobs write.
- Tests that checked a fact with a bare status update now write the check-log row first, as the verify route does (PV-02): the verify-duty guard (`test_reader_verification_duties.py`, edited with approval), the reassignment-check, fact-state-machine and fact-table contract tests. The late-check reassignment test accepts a refusal by error or by zero rows changed.
- The spot-check seed removes the parties through the purge marker row, not the old setting (PV-20).
- `test_a_cited_fact_moved_to_a_clearer_copy_...` moves the second fact to the live page too, because facts on a set-aside page are no longer citable (PV-14).

## Reviewer pass

An independent reviewer (opus, read-only) went through the whole diff and returned 12 findings. Eleven were confirmed and fixed test-first; one is recorded as policy.

| # | Severity | Finding | Result |
|---|---|---|---|
| 1 | High | Purge exemption was a setting any role can set | PV-20, fixed |
| 2 | Medium | `0226` ignored status and date columns, and never compared old values | Fixed in `0226` |
| 3 | Medium | Flag history readable across cases sharing a bundle | Fixed in `0228` |
| 4 | Medium | Seed left `recorded_by` null so isolation cases skipped | Fixed; cases now run |
| 5 | Medium | Pipeline set-aside omitted replaced pages | Fixed; shared helper |
| 6 | Medium | Two edits to existing tests, beyond seeds | Listed above, need approval |
| 7 | Low | Transcript with no hash row sent unchecked | Fixed (jobs from 2026-11-12) |
| 8 | Low | Re-read could orphan a checked fact's words | Fixed for one case; shared bundle is policy item 7 |
| 9 | Low | A failed job could block signing for good | Fixed |
| 10 | Low | Region `"unknown"` recorded for AI calls | Fixed in pipeline, trial, after serving: a missing region is a configuration error |
| 11 | Low | Concordance history append-only by grants only | Fixed; trigger |
| 12 | Low | Matcher conflict silently dropped a page | Fixed; weaker page kept as missing |

The reviewer checked and found sound: the role-based triggers on pages, documents and the audit log; hash compatibility of `0227` and `9009`; row-level security and purge cascade on the new tables; close, trial and offline download checks; after-serving signing; and the absence of patient text and forbidden words.
