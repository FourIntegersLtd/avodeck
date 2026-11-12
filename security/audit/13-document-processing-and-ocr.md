# 13. OCR, document parsing and file-processing security

Assessment date: 12 November 2026. Branch: `docs/audit-13-document-processing`. Scope: everything that happens to an uploaded file between the moment its upload completes and the moment its pages and words are stored: retrieval, decryption, type detection, unpacking, conversion, page counting, page cutting, rendering, text-layer reading, OCR, persistence and clean-up, and the worker those steps run in.

## Summary

- **No path was found by which one person's file can read, change or be mixed with another person's.** Storage keys are built from server-made ids only, each page is sent to OCR on its own and its words are written to the page that was sent, and every failure path logs codes only.
- **One malformed OCR answer could fail a whole bundle** (DP-01). The provider's answer was not checked; an empty or misshapen answer raised an error that was not treated as a failed page, so the job failed after three attempts.
- **Hostile or oversized files could exhaust the worker** (DP-02, DP-03, DP-04). Images had no frame or pixel limit, Word files, emails and pictures of up to 500 MiB were read whole, and nothing capped the number of pages a file could produce, each a paid OCR call.
- **Parsers ran as threads inside the worker** (DP-09). A page that hangs or balloons pikepdf, pdfium or Pillow could not be stopped and held up every bundle on that worker for up to six hours.
- **Two simultaneous splits on one case could clash on page numbers** (DP-12). Found by a new concurrency test.
- Smaller items: decrypted copies in the shared temp directory (DP-06), bodies read whole before their size check (DP-07, DP-13), an uncapped OCR response (DP-08), two ways of opening a damaged PDF (DP-05), a dependency audit that never failed CI (DP-10) and two documents out of date (DP-11).
- All thirteen findings are fixed on this branch, each after a test that failed first. What could not be verified here is listed under Unverified.

## How this was tested

- 58 tests in `api/tests/security/test_pipeline_document_processing_*.py` and `test_foundation_document_processing_*.py`, and 25 in `api/tests/unit/test_foundation_sandbox*.py`. Every file is synthetic and built inside its test (reportlab, pikepdf, Pillow, hand-made zips); limits are lowered in the test, so nothing really allocates gigabytes.
- No real provider was called. OCR answers come from fake HTTP transports and the recorded OCR stand-in; the virus scanner and the models were not contacted. No weaponised file was used anywhere.
- Each weakness had a test that failed for the stated reason before its fix (`test(...)` commit before `fix(...)` commit, same finding id). Log checks use a canary string as file name, page text and file content.
- Run against a separate local Postgres for this branch (port 59322) as the real `app_api` and `app_worker` roles.
- Each claim is marked **T** (a test), **R** (code read, with the line) or **U** (unverified). Tests held by audits 12, 15 and 16 were not repeated; they are cited where relied on.

## 1. Verified pipeline

| Step | What runs | Where | Notes |
|---|---|---|---|
| Upload | Chunked raw-body upload, 8 MiB chunks, 500 MiB a file, 20 open uploads a person, SHA-256 per chunk and per file | `api/app/core/routes/uploads.py` | No multipart, no presigned URL (R). Audit 12 |
| Storage | S3, keys `cases/{case}/uploads/{upload}/{n}` and `cases/{case}/pages/{page}/…` | `core/storage.py`, `pipeline/paths.py` | The client's file name is stored in a column only, never in a key or path (R, T) |
| Encryption | AES-GCM with a per-case key, the storage path as associated data | `core/crypto.py` | Decrypted one chunk at a time; the file's hash is re-checked |
| Virus scan | ClamAV, separate service, fails closed | `core/scan.py` | Audit 12 |
| Queue and worker | Procrastinate on Postgres; `worker` (bulk) and `worker-interactive`; 3 attempts, 6 h and 1 h limits | `core/jobs.py`, `worker/main.py` | Audit 15 |
| Type detection | Leading bytes only; the file name is used for `.eml` alone | `pipeline/convert.py` `sniff` | |
| Unpacking | `zipfile`, the standard email parser; 500 MiB, 2,000 members, depth 3, shared across nesting | `convert.py`, `service.py` | A zip with a locked member, or over a limit, is kept as an exhibit |
| Conversion | python-docx to typed pages; Pillow image frames to one page each | `convert.py` | Now capped (DP-02, DP-03) |
| Page count and cut | pikepdf, recovery off, one page at a time | `pipeline/parsing.py`, `core/pdf.py` | Now in a child process (DP-09) |
| Rendering | pypdfium2 at 150 dpi, at most 25 million pixels, JPEG by Pillow | `pipeline/render.py` | Now in a child process |
| Text | Embedded text layer if usable (pypdfium2), otherwise OCR | `pipeline/words.py` | |
| OCR | Azure AI Document Intelligence REST `2024-11-30` over httpx, one single-page PDF a call, bytes not a URL | `core/ai/azure_ocr.py` | Endpoint from the cell config and checked against the cell allow-list; redirects not followed (T) |
| Persistence | `app.pages`, `app.ocr_words` (text sealed per word), `app.page_analysis` | `pipeline/repo.py` | Words are deleted and re-inserted in one transaction, so a retry cannot duplicate them (T) |
| Clean-up | Decrypted temp file deleted on success, failure and cancellation; scratch directory swept at worker start | `core/case_files.py` | DP-06 |

Not present anywhere in `api/app`: `subprocess`, `os.system`, a shell, `eval`, `pickle` of file content, tar handling, ImageMagick, Ghostscript, LibreOffice, an OCR engine on our own machines, a fetch of any URL taken from a file (R).

## 2. Dependencies

| Library | Used for | Constraint | Risk |
|---|---|---|---|
| pikepdf (qpdf, native) | Open, count, cut PDFs | `>=10.17,<11` | Native parser on hostile input; now in a child process |
| pypdfium2 (PDFium, native) | Render, text layer | `>=5.14,<6` | As above |
| Pillow (native decoders) | Image frames, JPEG, picture hash | `>=13,<14` | Decompression bombs; now capped and in a child process |
| python-docx (lxml) | Word files to text | `>=1.2` | Entity expansion and oversized parts; parts now capped (T) |
| zipfile, email (standard library) | Archives, emails | Python 3.12 | Declared sizes; members now read under a hard byte cap (T) |
| httpx | OCR calls | `>=0.28.1,<0.29` | Response now size-capped |
| pypdf | Evaluation tools only, not the product | `^6.21` | Ten advisories at 6.21.1; lock moved to 6.21.4 |

`poetry.lock` pins exact versions. `pip-audit` in CI is now blocking with an empty ignore list (DP-10). The web audit (`pnpm audit`) is still report-only: it reports 20 advisories (3 critical, 1 high) that need triage first (see Policy items).

## 3. Malicious and malformed files

| Attack | Result | Mark |
|---|---|---|
| Path traversal in a file or zip-member name (`../`, absolute, NUL, very long) | The name never reaches a path or key. Control characters in member names were being stored; now cleaned (DP-03) | T |
| Zip with understated sizes, overlapping entries, too many members, nesting past depth 3, a locked member | Refused or kept as an exhibit; members are read through a hard byte cap, not on their declared size | T |
| Image claiming enormous dimensions or thousands of frames | Was decoded in full (DP-02); now refused on its header before any frame is decoded | T |
| Word file whose parts inflate past a cap, or carry entity expansion | Was unbounded (DP-03); now refused; entities are not expanded | T |
| Password-protected or truncated PDF | Marked `locked` or `damaged`, shown to the expert, the other files carry on | T |
| PDF declaring far more pages than it has, a huge page box, a zero-area page | The job does not crash; rendering stays under 25 million pixels; the shortfall is reported (audit 16, PV-11) | T |
| A page that hangs or crashes the PDF or image parser | Was unstoppable (DP-09); now killed at the time or memory limit and that page or file alone is marked | T |
| Command injection, arbitrary file read or write | No shell, no external program, no path built from file content | R |
| Request forgery through the OCR provider | Bytes are posted, not a URL; the polling address must be on the allow-list and the same host; redirects are not followed | T |

Privileges: the image runs as a non-root account with no volume. Before this branch the parsers shared the worker's process, and with it every secret in its environment. A parser child now starts with an allow-listed environment (PATH, LANG, LC_ALL, LC_CTYPE, TZ, library and Python paths; no secrets), only its own socket, no log or error-reporting handler and a memory limit (DP-09).

## 4. Resource limits at the execution boundary

| Limit | Value | Enforced in | Mark |
|---|---|---|---|
| File size | 500 MiB | Upload start and each chunk | T (audit 12) |
| Request body on whole-body routes | Each route's own cap, checked before reading | `core/request_body.py` `read_capped` (DP-07) | T |
| Body read for an idempotency key | 16 MiB read for the fingerprint, after the session check; larger declared bodies are fingerprinted by length and type, and the route's own cap applies | `core/http.py` (DP-13) | T |
| Zip contents | 500 MiB, 2,000 members, depth 3 | `convert.py` | T |
| Non-PDF converted in memory | 100 MiB (`MAX_CONVERT_BYTES`); plain zips exempt | `service.py` (DP-03) | T |
| Image | 100 million pixels a frame, 2,000 frames, a total budget (`MAX_IMAGE_PIXELS`, `MAX_IMAGE_FRAMES`) | `convert.py` (DP-02) | T |
| Pages | 4,000 a bundle and a document (`MAX_BUNDLE_PAGES`, `MAX_DOCUMENT_PAGES`), checked under the numbering lock before any page row exists | `service.py`, `repo.py` (DP-04) | T |
| Parser time and memory | 120 s a call, 3 GiB a child (`SANDBOX_*`) | `core/sandbox.py` (DP-09) | T; memory on Linux only |
| OCR call | 60 s, 5 attempts, 300 s of polling, response 64 MiB (`MAX_OCR_RESPONSE_BYTES`) | `azure_ocr.py` (DP-08) | T |
| OCR concurrency | Account-wide limiter | `service.py` | T (existing) |
| Job | 3 attempts, 5 stall requeues, 6 h or 1 h | `core/jobs.py` | T (audit 15) |

None of these depends on the browser. Still absent: a storage or page quota for an organisation (see Policy items).

## 5. Data leakage

- **Logs** (T, R). Every log call on these paths carries ids, counts, status codes or an exception's class name. The log record factory scrubs addresses, long digit runs and identifiers. With a canary as file name, page text and file content, no failure path wrote it to a log or to a stored error column.
- **Error reporting** (R). Sentry has local variables, source context, request bodies and breadcrumb data switched off, and exception messages are blanked before sending.
- **Stored errors** (R). `app.jobs.error_code` and the new failure codes are fixed words (`malformed_response`, `response_too_large`, `timed_out`, `out_of_memory`, `crashed`).
- **Temporary files** (T). Decrypted PDFs were in the shared temp directory (DP-06); now 0600 files in a 0700 directory, removed however the split ends and swept at start. `core/crypto.py` claimed plaintext never touched disk; corrected.
- **Caches** (R, T). The reader's word cache is keyed by case and holds plaintext in memory; the route checks access on every request before the cache is asked, and an existing test proves a warm cache gives nothing to someone without access.
- **Debug endpoints and analytics** (R). None serves OCR text or raw provider output; no analytics library is in the API.
- **External providers** (R). Azure Document Intelligence (UK South) receives the unredacted page: that is the purpose of OCR, it is in-region and it is in the DPIA table. Models receive text only after identifier stripping. The temporary direct Anthropic route (ADR 0036, dummy data only) was missing from the DPIA table; added (DP-11). Its start-up guard is audit 14's subject.

## 6. Processing isolation

- **Between cases** (R, T). One worker serves many cases. Each job runs under its case's row-level-security context; temp files have unique names; storage keys are per case; no module-level state holds file bytes between jobs.
- **Between a hostile file and everyone else** (T). Before: a hang in a parser thread blocked the single cut or render thread for every bundle on the worker. Now: the call is killed, the next call succeeds, and a second bundle is not held up by one bad page. Several hostile pages in one file can still occupy the two to four shared children for a bounded time; the number of time-failed cuts per document is capped.
- **Host files, environment and network.** The parser child has an allow-listed environment with no secrets and no inherited descriptors (T). It is not network-isolated or on a read-only filesystem; that needs platform support (see Policy items).
- **File-name collisions** (R). Names are never used to address anything.

## 7. Integrity of the resulting record

| Condition | Detected | Told to the expert | Mark |
|---|---|---|---|
| OCR returns no page, two pages or a page numbered other than 1 | Now a failed read for that page (DP-01) | Page listed as unreadable | T |
| OCR answer misshapen (missing box, confidence out of range) | Now a failed read (DP-01); a word with no box fails its whole page rather than being skipped, because skipping would drop text silently | As above | T |
| A page fails OCR permanently | Marked unreadable, job carries on | Bundle summary and the clearer-copy list | T (existing) |
| A page lost to a parser failure | Marked unreadable with no stored image (no blank stand-in), and cut again on a later run | As above | T for the pipeline side; the reader side is open (below) |
| File has fewer pages than it declares | Shortfall recorded | Missing-items list (audit 16) | T |
| Duplicate pages or words on retry | Unique constraints; delete-and-insert in one transaction | n/a | T |
| Two splits numbering at once | Was a clash (DP-12); now serialised under one lock | n/a | T |
| Text on the wrong page | Not reachable: one page a call, written to the page sent; the answer's page number is now checked | n/a | T |
| Count and cut disagreeing on a damaged file | Was possible (DP-05); both now open without repair | Marked `damaged` | T |
| File or page altered in storage | Hash re-checked before use | Marked `altered` (audit 16) | T (audit 16) |
| Several records merged into one file | Not detected: a document is an uploaded file | No | R, by design |

Found by the review and fixed on this branch: the reader's `POST /pages/{n}/read` now refuses (409) a page the parser lost and that has no picture, and the page image and second-reading routes answer "not available" for it; migration 0076 lets such a page become readable once it is cut again and read (and nothing else clears `unreadable`); the start-up sweep removes stale `unpack-*` scratch directories. Audit 16 has since merged into this branch: its `page_analysis.read_failure` allows only `ocr_failed` and `altered`, so a lost page is still told apart from a blank scan by `text_source` being null and its image being unset; a `read_failure` value of its own for a lost page (migration, and the reader, report and final-check slices' handling) is left open.

## 8. Findings register

| Id | Severity | Finding | Evidence (before the fix) | Verified | Fix | Owner |
|---|---|---|---|---|---|---|
| DP-01 | Medium | OCR answer not validated; one bad page failed the bundle; extra pages dropped silently | `azure_ocr.py:165-199`, `service.py:691` | T | (Fixed: `8fa2ec5e`, `32d675b5`) | foundation, pipeline |
| DP-02 | Medium | Images decoded with no frame or pixel limit, all frames held at once | `convert.py:262-271` | T | (Fixed: `c3a70121`) | pipeline |
| DP-03 | Medium | Non-PDFs up to 500 MiB read whole; Word parts and typed text uncapped; control characters in stored member names | `service.py:288-296`, `convert.py:125-151` | T | (Fixed: `eb897d2e`) | pipeline |
| DP-04 | Low–medium | No page cap at the worker | no cap in `repo.py:71-97` | T | (Fixed: `4e8a7b6b`, migration 0147, reason `too_many_pages`) | pipeline, foundation |
| DP-05 | Low | Cut opened with repair on, count with repair off | `service.py:296` against `:196` | T | (Fixed: `aca56061`) | pipeline |
| DP-06 | Low | Decrypted PDFs in the shared temp directory, left behind by a crash; docstring wrong | `service.py:164,293`, `crypto.py` | T | (Fixed: `4aed9e7a`, `5e0eeb30`) | foundation, pipeline |
| DP-07 | Low | Two routes read the whole body before checking its size | `setup/routes_files.py:73`, `trial/common.py:122` | T | (Fixed: `56b0053b`) | foundation |
| DP-08 | Low | OCR response body uncapped | `azure_ocr.py` | T | (Fixed: `8fa2ec5e`) | foundation |
| DP-09 | Low | Parsers ran as unkillable threads with the worker's memory and secrets, including the two-page-scan cut in the API and the picture hash | `service.py:112-113`, `split.py:13`, `duplicates.py:86` | T | (Fixed: `48482c95`, `eeae459f`, `55c5f116`, `c695ff29`, `1121c184`) | foundation, pipeline |
| DP-10 | Low | Dependency audit never failed CI | `.github/workflows/ci-api.yml:143-154` | R | (Fixed: `d52f7fba`, `b41a0ec4`) | foundation |
| DP-11 | Info | F4 said 3 tries (it is 5 attempts); DPIA table omitted the temporary direct model route | `docs/spec/failure-modes.md`, `docs/compliance/dpia.md` | R | (Fixed: `245e5dc9`) | foundation |
| DP-12 | Medium | Two simultaneous splits on one case could give clashing page numbers | `repo.add_pages` | T | (Fixed: `aca56061`) | pipeline |
| DP-13 | Low–medium | A request with an idempotency key had its whole body buffered before any route cap | `core/http.py:258` | T | (Fixed: `73eac601`) | foundation |

## Policy items, with the default taken

- **Web dependency audit stays report-only** until its 20 advisories are triaged. Default: triage as a separate task, then make it blocking.
- **No organisation quota** on storage or pages. Default: none added; the per-person request limit, 20 open uploads and the new page cap bound one bundle, not a determined account. Belongs with billing.
- **Parser child is not network-isolated or read-only.** Default: process isolation with an allow-listed environment now; a separate locked-down conversion service is recorded as deferred.
- **A split request whose cut is killed returns 422 `invalid`.** Default taken by the pipeline owner; the wording is for the reader owner to confirm.

## Unverified

- The sandbox memory limit is honoured on Linux only. It was checked in a Linux container; on macOS the test is skipped.
- The rule guard suite's database-backed tests could not run locally (no Auth service on this branch's stack). CI runs them on the PR.
- The OCR answer validator has not been run against a real Azure analyze result: no raw fixture exists. The 2024-11-30 schema makes a word's polygon optional; the validator treats a missing one as a failed page. Record a real Layout response as a fixture before live bundles.
- Azure's real behaviour on hostile files was not tested, by instruction: no crafted file was sent to a third party.
- Real-world throughput of the sandbox on a 1,000–4,000 page bundle was not measured; only a 100-page synthetic split was timed (see Remediation).
- Whether Railway enforces a container memory limit for the worker: none is set in `railway/worker.json`.

## Remediation, in priority order

All thirteen findings are fixed on this branch. Remaining, in order:

1. Audit 16 (#320) and audit 15 (#306) are merged into this branch (a merge, not a rebase). Migration 0147 lists audit 16's `altered` reason and `too_many_pages`, so it is correct on top of 0146; the foundation guard migration is 0076 because audit 16 took 0074 (its `pages_guard` is kept inside 0076). Failure-mode rows of this audit are F123-F131 (audit 16 took F110-F122). A new `read_failure` value for a lost page is the one follow-up that remains.
2. Time a real 1,000-page bundle with the sandbox on before any live case.
3. Triage the web dependency advisories and make that audit blocking.
4. Set a container memory limit for the worker services.

### Fixed on this branch

- DP-01 to DP-13 as in the register. New failure-mode rows F123–F131, each naming its test; `too_many_pages` added to the state machines and data dictionary; new interface wording `bundle.unopened_too_many_pages`.
- Three expected-failure markers from audit 12 (member names, 4,001 pages, image frames) are removed: those weaknesses are closed by DP-03, DP-04 and DP-02.
- Sandbox speed. The first version roughly doubled a 100-page synthetic split (about 8.5 s to 15–18 s) because each new child took about 2 s to start. Children are now started and warmed in the background before they are needed (`1121c184`). Measured on a loaded machine (load 8–10): before the sandbox 8.1–8.9 s; with it and warm children 7.0–8.3 s; with cold children 13.5–15.9 s.
- DP-13 residual: a keyed request over 16 MiB sent without a declared length is refused 413, because the bytes cannot be un-read; at most 16 MiB is held per signed-in request.

## Reviewer pass

An independent read-only review (opus) of the full diff found twelve items. Fixed on this branch: sandbox slot hand-over after a cancelled call, per-call ids on sandbox replies and single-owner closing of connections, the fingerprint buffering before sign-in, sandbox stop on API exit, a double pickle, the unpinned dependency audit, the shared scratch directory (now a private 0700 directory per user), parent-death handling for children, and the pipeline items listed in the register notes below. Not changed: an OCR word with no box still fails its page (see section 7). Needs another owner: audit 16's migration 0146 (see Remediation 1).
