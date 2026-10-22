# 0017 A fixed century rule runs at upload and is flagged, not guessed

Date: 2026-10-22 · Status: accepted (refines the "never guessed" note in the `gb-eaw` pack and architecture 3)

**Problem.** In a handwritten 1980s and 1990s bundle most entries sit in "Not on a line yet" on the timeline. Dates such as `16.6.87` have a two-digit year, so the date normaliser marks them "Please check" and gives no `event_date`. The century rule already exists but only ran at verify. Results and prescriptions cut from a consultation line also carry no date of their own. The pack says ambiguous dates are "flagged, never guessed".

**Decision.**
- A fixed rule whose basis is visible on the fact is **flagged**, not **guessed**. At upload the rule may set `event_date`, and `facts.date_basis` records which rule did it.
- **Anchor.** The latest unambiguous (four-digit year) printed date across the case's pages; if there is none, today's date.
- **Two-digit year.** The century is the latest one whose date (same day and month, pack date order) falls on or before the anchor. Basis `printed_century`.
- **Date from the line above.** For a fact with a date field whose own words hold no readable date, take the nearest printed date on the same OCR line or the line immediately above, in reading order. Never a date below. The date's word ids are added to the fact's cited words, so provenance still covers it (rule 2). Basis `line_above`.
- **Mangled dates** (for example `181.88`, `27697`) stay undated. No repair is attempted.
- **The expert always overrides.** Verify or correct replaces the suggested date and basis. Only verified or edited facts can be cited (rule 2).
- Date order, and so the reading of day and month, still comes from the pack (rule 11). Core tests run under the fixture packs.

**Why.**
- The date is always read from cited printed words by a fixed rule, never chosen by a model, so rule 1 and rule 2 hold.
- The mark on the timeline ("check the date") tells the expert what to look at; hiding the entry from its lane tells them nothing.
- Pre-verify placement lets the expert see the timeline of a whole bundle before reading every page.

**Consequences.**
- `date_basis` takes four values: `printed`, `printed_century`, `line_above`, `please_check`. The database check already allows `printed_century`; a new migration is needed only if it does not allow `line_above`.
- The timeline shows a mark for `printed_century` and `line_above`. Contracts only grow.
- Failure mode F12 gains two bases and tests in `api/tests/repository/test_pipeline_date_repair.py`.
- Revisit if experts correct these dates often; then show them as "Please check" again until verified.
