# Capacity numbers

What was measured, when, and how. Add a section per run; never overwrite an old one. Numbers here are the
evidence for the settings in `docs/ops/scalability-plan.md` (this file is created by PR 6; PR 18 adds the
load-test runs).

## Split memory (scalability plan PR 6, 2026-11-05)

Measured with `tracemalloc` (Python allocations only: PDFium, qpdf and the JPEG encoder allocate in C and
are not counted) around `service._split`, by `api/tests/repository/test_pipeline_split_memory.py`: a
generated 1,500-page PDF (72 MB: each page carries 48 KB of random bytes), stored in 8 MB sealed parts,
one worker, the picture step stubbed so only the split is measured, page objects counted and dropped by
the storage stand-in.

| Split | Peak Python memory added | Why |
|---|---|---|
| Before: whole document joined into one `bytes`, every page's PDF kept in a per-document cache | 146 MB (75 MB on a 36 MB document) | document parts + their join + all page PDFs, about twice the document |
| After: parts streamed to a temp file, pages cut one at a time from the file, none kept | 36 MB (also 36 MB on the 36 MB document) | one sealed and one opened 8 MB part while downloading, plus the few pages in flight |

The test limit is 58 MB, 40% of the "before" figure. After the change the peak no longer grows with the
document (a 1,500-page, 400 MB bundle costs the same Python memory as a 36 MB one); the "before" figure
grew about twice as fast as the document.

Also changed by PR 6: one split runs at a time per worker process (`_SPLITS`, a semaphore of 1), and the
temp file is deleted on success and failure. Temp files go to the system temp directory (`TMPDIR`), one at
a time per document being cut, so a worker needs scratch disk of about one document (the biggest upload
allowed).

Not done in this run (needs live infrastructure): the resident-set measurement of a 900-page dev bundle on
Railway and the raise of `WORKER_CONCURRENCY` to 8 (then 12 after PR 7). Do those with the steps in the
plan, and record the RSS below.

### RSS on a 900-page dev bundle (to fill in)

| Date | Bundle | RSS per job | `WORKER_CONCURRENCY` chosen |
|---|---|---|---|
| | | | |

## Page-batch jobs (scalability plan PR 17, flag `PIPELINE_BATCH_PAGES`, default 0 = off)

With the flag on, a bundle is split into `read_page_batch` child jobs of about `PIPELINE_BATCH_PAGES` pages (two phases:
OCR, then extraction and embedding) on the bulk queue with no case lock, and the parent `process_bundle` is parked
between phases and woken to plan the sort and to run the whole-bundle tail (duplicates, missing items). See ADR 0038.

| Number | Value | Where it comes from |
|---|---|---|
| Pages per batch | `PIPELINE_BATCH_PAGES` (100 in production once PR 18 has measured it) | env |
| Batches reading at once per bundle (window) | `PIPELINE_BATCH_WINDOW`, default 2, at most `WORKER_CONCURRENCY - 1` | env; the worker warns at start when above |
| Pool checkouts per batch-mode job (cap) | `max(1, (WORKER_DB_POOL_MAX - 2) // WORKER_CONCURRENCY)` = 2 at pool 10, concurrency 4 | `app.core.jobs.checkout_cap` |
| Pool checkouts per bundle | at most `window x cap + cap` = 6 | the parent is parked while children run |
| Pool checkouts, four batch-kind jobs | at most 4 x cap = 8, the pool less its reserve | |
| Widest `bounded()` in batch mode | `cap` | `pipeline.service._width` |

A lone bundle is slower per page than today unless the pool grows (about 4 OCR pages in flight against about 8); the
trade is that fifty bundles no longer queue behind each other's whole run. PR 18 measures the 4,000-page time against the
75-minute target and tunes `WORKER_DB_POOL_MAX`, `WORKER_CONCURRENCY` and the window together before the flag is set in
production. The bulk queue's oldest age (`GET /v1/internal/metrics`) shows a backlog of children.

## Load test (scalability plan PR 18)

Scripts: `scripts/load/` (k6; read its README first). Targets: ADR 0037, NFR C3 (50 bundles at once), C5 (300
experts online), P2 (4,000 pages in 75 min at p90), P3 (reader page p95 300 ms), P9 (API p95 400 ms). Run only
against the Railway `staging` environment with its own staging Supabase London project and synthetic bundles (stitched to
4,000 pages, half with text layers and half image-only). Never against production; never with real records.
Add a row per run and never overwrite an old one. The two right-hand columns are for you to fill in after each run:
the numbers k6 prints and the ones you read from Railway and `GET /v1/internal/metrics`.

| Run | Load | Pass when | Date and build | Measured | Result |
|---|---|---|---|---|---|
| Bundles x5 | 5 bundles at once (`bundles.js`, `LOAD_BUNDLES=5`) | p90 at most 75 min; 0 failed jobs; retried 429s under 5% of calls; worker RSS under 80% of the limit; no job run twice | | | |
| Bundles x20 | 20 bundles at once | same as above | | | |
| Bundles x50 | 50 bundles at once | same as above | | | |
| Interactive | `rerun_checks`, `build_report`, `build_trial_pack`, `export_case` every 30 s during a bundle run (`interactive.js`) | p95 time to start at most 30 s | | | |
| Experts x300 | 300 users ramped over 10 min; one case stream each, a page every 20 s (page, image, facts), a search every 5 min, the timeline every 10 min (`experts.js`) | API p95 at most 400 ms; reader page p95 at most 300 ms; 5xx under 0.1%; 503 `busy` under 0.5%; loop lag p99 at most 100 ms | | | |
| Soak | 2 h at 300 experts (`LOAD_HOLD=2h`) with one worker deploy | no job fails; bundles resume after the deploy | | | |
| Bundles x50 after PR 17 | the 50-bundle run again once bundles are split into page-batch jobs | same as the 50-bundle row | | | |

### Where each number comes from

- Bundle minutes (p90), failed jobs, time to start, API and reader p95, 5xx and `busy` rates: k6 summary (`bundle_minutes`, `failed_jobs`, `time_to_start_seconds`, `http_req_duration`, `server_errors`, `busy_503`).
- Loop lag p99, pool waiting, queue age: `GET /v1/internal/metrics` (the scripts read loop lag when `OPS_METRICS_TOKEN` is set).
- Worker RSS against its limit: Railway metrics for the `worker` and `worker-interactive` services.
- Retried 429s as a share of AI calls: provider 429 counters in the same metrics against `ai_calls` for the run.
- Jobs run twice: `app.jobs` attempts for the run's cases: any job with more than one start that did not fail first.
