# Worker (bundle pipeline and background jobs)

Read the root `CLAUDE.md` and `backend/CLAUDE.md` first. The worker shares the backend codebase (`backend/app`) and runs as a separate App Platform component that consumes the Postgres job queue.

## Job types

| Job | Owner slice | Does |
|---|---|---|
| `process_bundle` | A | Split → render → OCR → strip identifiers → per-page extraction → dates → duplicates → gaps → missing items → index |
| `process_pages` | A | Same, for appended pages only |
| `extract_questions` | C | OCR letter of instruction → T-06 |
| `rerun_checks` | D | Re-run "Things to look at" |
| `build_report` | F | Assemble the report in the expert's Word template |
| `final_check` | F | 28 checks |
| `verify_opposing` | G | T-21b/c/d |
| `match_trial_bundle` | I | OCR (Read) + page matching |
| `build_trial_pack` | I | Large-print PDF |
| `export_case` | H | ZIP of the record of work |
| `delete_case` | H | Scheduled deletion + certificate |

## Rules

- **Idempotent per step and per page.** A job can be retried safely; every step checks whether its output exists before redoing work.
- **Bounded concurrency** for Azure OCR and Bedrock calls; respect rate limits with back-off.
- **Progress:** update `jobs.pct`, `jobs.step`, `jobs.eta_seconds`. No patient text in job rows or logs.
- **Pages keep their numbers forever.** `bundle_page_no` is set once. Appended records continue from the last number.
- **Duplicates are set aside, never deleted.**
- **Never auto-correct handwriting.** Low-confidence pages go to the "Pages we couldn't read" queue (screen 10).

## AI pipeline rules (from docs/architecture.md section 6)

1. `pii.strip()` before every Claude call, using the case's pack identifier recognisers (NHS number for `gb-eaw`). Mask identifier regions on images sent to vision (T-10).
2. One Claude call per page for extraction (T-07b), using the cached system prompt + JSON schema. Use Bedrock batch where available in-region.
3. Every extracted item lists `word_ids`. `provenance.check()` rejects values not present in those words. Retry once, then mark `needs_human`.
4. Dates are normalised by code (`dateparser`) using the pack's date order (DMY under `gb-eaw`, MDY under the `zz-test` fixture); ambiguous dates are flagged "Please check", never guessed. The model's date is never final.
5. "As the clinician saw it" visibility is computed by code from `availability_evidence`, never decided by the model.
6. Every prompt has a version string. Changing a prompt or model requires `make eval` to pass.
7. Prompt templates are neutral: fact kinds, field schemas, record cues, vocabulary and date order are injected from `pack.prompt_context()`; output schemas are generated from the merged registries.

## Testing

- Pipeline tests run against synthetic bundles in `evals/gb-eaw/clin-neg-breach/general-practice/bundles/` with recorded OCR and Claude responses in `backend/tests/fixtures/`.
- Expected facts with page and word references live in `evals/gb-eaw/clin-neg-breach/general-practice/expected/` (one folder per pack combination). Slice A's acceptance tests compare pipeline output to them.
- `make eval` runs real models nightly and fails if extraction precision drops below 0.95 or the share of pages sent to screen 10 rises by more than 5 points from the last baseline.
