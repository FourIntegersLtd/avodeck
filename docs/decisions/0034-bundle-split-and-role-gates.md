# ADR 0034 — Split the bundle from the case; role-conditional blind read and verification

**Status:** Accepted
**Date:** 2026-11-01

## Context

`pages.case_id` was a direct FK, and `case_id` appeared on every page-level table (`pages`,
`documents`, `ocr_words`, `page_analysis`, `page_embeddings`, `document_parts`,
`document_scans`, `exhibits`, `record_entries`), in their RLS policies, in cross-table
integrity triggers (`facts_guard`, `pipeline_same_case_page`), and in the provenance chain.
Retrofitting a shared bundle after real case data existed would mean migrating every page's
and fact's provenance — the worst migration shape in the system. Every future channel (a firm
sharing a bundle across cases, a replacement case after a withdrawal) needs shared pagination
now, before launch.

Separately, blind read (`reading.py`'s 423 gate) and fact verification (`facts_guard`'s
named-expert check) were hard-coded to `named_expert`. Rule 9 already allows other case roles;
non-expert roles that read pages without ever giving evidence should not need to pass blind
read, and only roles the pack names as carrying verification duty should be able to verify a
fact.

## Decision

### Bundle split

- A new `app.bundles` table becomes the structural parent of pages and documents.
  `cases.bundle_id` points at it; multiple cases may reference the same bundle. The unique
  constraint on page numbering moved from `(case_id, bundle_page_no)` to
  `(bundle_id, bundle_page_no)`.
- `app.can_access_bundle(bundle_id, action)` is the new entry point for page-level RLS: true
  if any non-deleted case referencing the bundle passes `can_access_case` for that action.
- `case_id` is dropped from every page-level table. Tables that are genuinely per-case even
  when the bundle is shared — `facts`, `page_reads`, `fact_verifications`, `fact_flags`,
  `citations`, `missing_items`, `record_gaps`, `second_readings`, `transcript_views`,
  `concordance`, `page_quarantines`, `page_replacements`, `word_corrections`, `page_turns`,
  `page_notes`, `witness_claims`, `witness_comparisons` — keep `case_id` (or, where they have
  none of their own, find the case through `page_id`/`document_id`) because they represent one
  case's reading of the bundle, not the bundle itself.
- A bundle cannot cascade-delete with a case: `bundles.source_case_id` is lineage-only
  (never read at delete time; `app.purge_case_rows` explicitly skips `bundles`), so a bundle
  outlives the case that created it. `PURGE_ORDER`'s terminal table is therefore `bundles`,
  not `cases`.
- **Drop order across slices.** Dropping `pages.case_id` had to wait until every slice whose
  tables reference it directly in an RLS policy had moved to `bundle_id` — including slices
  numbered after foundation's own migration range. `0044_foundation_bundle_drop_case_id.sql`
  drops `documents.case_id` only; the `pages.case_id` drop itself runs in
  `0222_reader_bundle_pages_case_id.sql`, after reader's `word_corrections`/`page_turns`/
  `page_notes` policies are rewritten against `bundle_id` in `0221`. Pipeline's own page-child
  tables follow the same pattern in `0129_pipeline_bundle_drop_case_id.sql`, after `0127`/`0128`.

### Role-conditional blind read and verification

- The case-type pack declares `blind_read_duties` and `verification_duties` (each
  `minItems: 1`, defaulting to `[named_expert]`). A pack-lint rule rejects any case-type pack
  that omits `named_expert` from either list, so blind read and verification can never be
  switched off for the expert whose name is on the report.
- `cases.verification_duty_roles` is set from the pack at case creation
  (`api/app/core/routes/cases.py`) and backs `facts_guard`'s dynamic, per-case check, avoiding
  a pack lookup on every fact write.
- `PageReadGate.__call__` (`reading.py`) now enforces the 423 only for a case role present in
  the pack's `blind_read_duties`; other roles see facts without having read the page first.
- `counts_for_citing` is unchanged: only the named expert's reads make a fact citable, even in
  a pack where a co-expert also carries blind-read duty. The report is signed by the named
  expert, so only their reading unlocks citation.

## Consequences

- Every Python query that found a case through `pages`/`documents`/page-child tables by
  `case_id` was rewritten to resolve `bundle_id` instead (`pipeline/repo.py`, `reader/repo.py`,
  `reader/transcript.py`, `understand/repo.py`, `write/routes_witness.py`, `report/assemble.py`,
  `report/checks.py`, `after_serving/common.py`, `trial/`, `core/contracts/*`,
  `core/routes/uploads.py`, `core/housekeeping.py`, `core/step_progress.py`).
- `can_access_bundle` adds one subquery across cases per page-level access check; for the
  common single-case bundle this is a one-row lookup against the `cases_bundle` index.
- A pack that wants a second role to carry blind-read or verification duty adds it to the
  relevant list; the static `case_action_allowed` matrix for `fact.verify` must also list that
  role, since the trigger's dynamic check and the static permission check are independent.

## Migration repair note (2026-11-01)

A follow-on change initially fixed the `case_id`→`bundle_id` gap in `word_corrections`,
`page_turns`, `page_notes`, `witness_claims` and `witness_comparisons` by editing their
already-applied migration files in place. That breaks "contracts only grow" (AGENTS.md) and
is what caused `supabase db push --linked` to fail: cloud still had the original policies live
at the point the new, lower-numbered `0044` tried to drop `pages.case_id` out from under them.
The six files were reverted to their original, already-applied content; the fix now lives in
three new migrations (`0221`, `0222`, `0509`) ordered after everything they depend on.
