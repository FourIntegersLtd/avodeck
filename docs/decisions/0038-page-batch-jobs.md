# 0038 Page-batch jobs: read a bundle in child jobs that take no case lock

Date: 2026-11-05 · Status: proposed (design; build is scalability PR 17) · Decided by: pipeline-owner, for the founder's review · Builds on: ADR 0037 (capacity plan), ADR 0021 (Railway, no data at rest), ADR 0013 (restricted roles) · See `docs/slices/A-pipeline.md` "Page-batch jobs" and `docs/ops/scalability-plan.md` PR 17

**Problem.** One `process_bundle` job reads a whole bundle (1,000 to 4,000 pages) while holding the lock `case:<id>` and one worker slot. Two effects: the expert's own `rerun_checks`, `build_report` and `export_case` wait behind it for up to 75 minutes (they take the same lock), and a single bundle holds a slot for that long, so 50 bundles at once need 50 slots of long jobs and one crash redoes the step queue from the last whole-step checkpoint.

**Decision.** Split the work that is per page from the work that is across pages.
- Per page (OCR, extraction, embedding) runs in child jobs of about 100 pages (kind `read_page_batch`, `bulk` queue, no case lock), at most `PIPELINE_BATCH_WINDOW` (6) of one run queued or running at once.
- Across pages (wrong-person suggestion, duplicates, gaps and missing documents, ready) stays in the parent job, under the case lock, run once after every batch is finished.
- The parent is still the only job the person sees. It plans the batches, **parks** (stays `running`, no queue row, no slot, no lock), and is woken once by the last child through `app.pipeline_complete_batch`, which changes `pipeline_runs.state` from `waiting` to `ready` under a row lock and defers the parent's queue row in the same transaction. That makes the fire exactly-once and crash-safe; a 30-second sweep (`app.pipeline_heal_runs`) repairs a lost fire.
- Progress is written to the parent's row from the database counts, so the stream, the screens and the API are unchanged. The flag `PIPELINE_BATCH_PAGES` defaults to 0 (today's single job).

**Alternatives rejected.**
- *A new job status `waiting`.* It would reach the OpenAPI client, every screen's status mapping, the `app.jobs` guards and the support actions. A parked `running` row needs none of that.
- *The parent waits inside its slot, holding the lock.* Simplest, but it keeps both problems (slot and lock for 75 minutes) and removes the point.
- *A separate "finish" job row.* A second user-visible row to hide from retry, cancel, summary and SSE, and a second thing to keep in step with the parent's pause and failure.
- *Hooks in the worker to mirror a child's pause or failure onto the parent.* Faster than a 30-second sweep but spreads pipeline logic into `app/core/jobs.py`. The sweep keeps the foundation change to four small hooks (`JobParked`, `progress(job_id, monotone)`, `JobRegistry.sweep`, `busy_processing`). Latency of up to 30 seconds on a pause or failure notice is acceptable for jobs that take an hour.
- *Batching everything including duplicates and T-07c.* Larger win, but both look across pages and need a merge step. Measure the tail in PR 18 first.

**Consequences.**
- The shared Azure OCR slot (PR 4) and the model slot (PR 3) remain the bound on provider calls; more children only wait. Each child caps its in-flight pages so four children do not starve the database pool.
- Pages are written without the case lock. This is safe because ranges are disjoint, nothing reads unverified suggestions as fact (rules 2 and 3), and stores check `not quarantined` at write time. Tests cover it.
- Kinds that used the lock to wait for the bundle (`reprocess_case`, `retry_unread_pages`, `extract_entries`, `repair_fact_dates`) pause with the resumable code `busy_processing` while a run is open.
- Runs of one case finish in plan order so a later tail never judges a page that an earlier run is still reading.
- New tables `pipeline_runs`, `pipeline_batches` (numbers and ids only, rule 6); next free migrations 0136 to 0138 and purge-order splice 1024.
- Rules 1, 2, 5, 6, 7 and 11 hold unchanged because the children call the same read, extract and embed code.
- Rollback is the flag: set `PIPELINE_BATCH_PAGES=0`; a run already planned still finishes the way it was planned.
