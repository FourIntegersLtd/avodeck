# 0016 Guideline versions are read from the overlay pack, not a database table

Date: 2026-10-18 · Status: accepted (refines 0005)

**Problem.** Decision 0005 and architecture 4 describe a hand-curated `guideline_versions` table, seeded from the packs. Slice D answers "which version of a guideline was in force on date X" and asked whether it can read the overlay pack's `guidelines.yaml` directly (`MergedPack.guidelines()`) instead of keeping a synced copy in Postgres.

**Decision.**
- There is no `guideline_versions` database table and no seed or sync job. The curated versions live in the overlay pack's `guidelines.yaml` (schema `overlay.guidelines.schema.json`) and are read with `pack.guidelines()` and `pack.guideline_sources()`.
- The "table version" in architecture 10.5 is the overlay pack version (`pack.versions["overlay"]`) together with the file's `reviewed_at`.
- A case records the version it was shown in slice D's `case_guideline_versions` (code, version label, `reviewed_at`), so a later pack change to a cited entry shows a notice.
- Everything in 0005 still holds: labels, dates and links only; no guideline text goes to any AI.

**Why.**
- Rule 11: guideline sources and versions are jurisdiction × specialty data, which is what overlay packs are for. A second copy in Postgres could drift from the pack the case was set up with.
- Each region cell already ships its own packs, so there is nothing to sync across cells (rule 5).
- Packs are schema-checked at start-up and reviewed in pull requests (`packs/**` is foundation-owned), which replaces "Avodeck clinical reviewer only" edits to a table.

**Consequences.**
- Changing a guideline entry needs a pack release, not a data edit. With the 20 to 30 guidelines in 0005 that is acceptable.
- Revisit if guidance must change faster than releases, or if per-row licence tracking outgrows the pack file; then add a table seeded from the pack, keeping the pack as the source.
