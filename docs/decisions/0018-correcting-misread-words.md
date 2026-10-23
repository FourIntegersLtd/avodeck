# 0018 The expert may correct the computer's reading of some words

Date: 2026-10-23 · Status: accepted (refines rule 2 in AGENTS.md and architecture 6.2.3)

**Problem.** The computer misreads handwriting ("3097 texturas booster giver" for "3/9/97 tetanus booster given"). Rule 2 says a value must be found in the words it cites, so the expert's correct reading could never be saved through "Change a detail". The only way out was typing out the whole page, which is far too much for one wrong line.

**Decision.**
- In "Change a detail" the expert can tick "These words were read wrongly" and type what the chosen words really say (`corrected_words {word_ids, text}` on `POST …/facts/{id}/verify`, action `edit`).
- The corrected words must be among the words the fact cites, in one unbroken run on the page. The value must then be found in the cited words with the expert's reading in place of those words; otherwise 422 `invalid`, as before.
- The expert's reading is kept in `word_corrections` (slice B), sealed with the case key like every word. The computer's reading in `ocr_words` is never changed. `fact_verifications.old_value` keeps the old value.
- A later reading of any of the same words replaces the earlier one (`superseded_at`). Any other checked fact on those words that the new reading no longer shows is flagged "Please check".
- Facts citing a current correction carry `cites_correction` ("Read by you"). `core/contracts/document_words` gives other slices the expert's reading for those words, and `citable_facts()` carries `cites_correction`.
- Only the named expert corrects (`fact.verify`). "Type out this page" stays the way for pages misread throughout.

**Why rule 2 still holds.** Every value still points to an exact run of words on a numbered page. The reading it relies on is shown as the expert's own, not the computer's, and both readings are kept.

**Consequences.**
- `word_corrections` has no `case_id` column. Migrations 0380–0799 set the purge order with fixed lists, so a table added in 0209 could not stay in that order. Rows belong to a page, row-level security finds the case through `pages`, and the rows go with their page when a case is deleted (tested). If the purge order later becomes additive, a `case_id` column can be added.
- Slices that read `ocr_words` directly (rather than through `document_words`) still show the computer's reading until their owners switch to the contract.

**Follow-ups (2026-10-23, migration 0211).**
- Word ids are places on the page, so when slice A replaces a page's words (turned, read again, typed out), every current reading on that page is replaced in the same transaction. A checked fact that relied on one is flagged "Please check" unless the new words show it.
- Deleting a quarantined page (`purge_quarantined_page`) deletes its readings.
- A new reading re-checks the facts on every word of the readings it replaces, not only its own run.
- Rule 8: a reading may not change words cited by a fact in a served or withdrawn report, nor replace a reading of them (409 `served_use_supplementary`). Not right on such a fact is refused the same way.
- Where the reading lands: on the run's real first word; when the caller holds only some words (a fact's cited words) and that word is not among them, on the first of them in the run. The reader reads a fact's words the same way (`apply_corrections(..., cited=)`), so it and `cited_words` agree.
- `fact_verifications.correction_id` names the reading a check relied on.
