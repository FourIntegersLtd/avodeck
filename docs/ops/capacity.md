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
