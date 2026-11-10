# 12. File upload, storage, encryption and file lifecycle

Audit date: 10 November 2026. Branch `audit/file-lifecycle`, from `main` at `1cadc5a5`.

## Summary

The upload protocol, the storage layout and the encryption held up under test. No test found a way for an outsider to upload, read or alter a file, and a normal case deletion leaves no readable copy in storage or the database.

The weaknesses are around the edges of that core:

- **One broken feature (high):** the named expert's full export fails for any case that holds a really uploaded document.
- **Virus scanning is advisory in three places (medium):** a flagged or unscanned original can be downloaded, a cell with no scanner configured silently uses a test stand-in, and instruction letters are read before any scan.
- **Hostile files can exhaust the worker (medium):** nested archives escape the unpack limit, and one very large PDF page asks the renderer for about 3.6 GB.
- **Damaged PDFs lose pages without a sign (medium).**
- **A partly failed storage delete is certified as complete (medium).** The leftover object cannot be read, because its key is destroyed.

Key management is the main thing this audit could not settle: the master key is an environment variable, KMS is not implemented, and there is no key rotation.

## How this was tested

- 176 new tests in `api/tests/security/test_foundation_file_lifecycle_*.py`: 140 pass, 36 are strict expected failures.
- Every test asserts the safe behaviour. A strict expected failure reproduces a weakness; its `reason` carries the id used below.
- Every "refused" result has a control in which the rightful owner succeeds on the same request against a real stored file.
- The tests ran against a local Postgres with every migration applied, and a local S3 stand-in (moto) holding real sealed objects. Only synthetic files were used.
- An independent reviewer checked every expected failure against the application code. Four were reworded or downgraded as a result (V2g, V2d, V4a, V1c).
- Nothing in a cloud account was examined. See "Unverified".

Run the suite from `api/`:

```
TEST_DATABASE_ADMIN_URL=… TEST_DATABASE_URL=… TEST_DATABASE_URL_DIRECT=… \
  python -m pytest tests/security -q -rx
```

## 1. Verified upload sequence

| Step | What happens | Where |
|---|---|---|
| Start | `POST /v1/cases/{case}/uploads` with `kind`, `filename`, `size`, whole-file `sha256`. Needs a session, CSRF token and `case.upload`. Creates an `app.uploads` row, status `open`, random UUID id, expiry 7 days. | `api/app/core/routes/uploads.py:165-203` |
| Transfer | `PUT /v1/uploads/{id}/chunks/{n}`, raw body, `X-Chunk-SHA256` header. Parts are 8 MiB, size fixed by the server. Each part is checked for number, exact length and checksum, sealed with the case key, and written as its own object. | `uploads.py:242-310` |
| Status | `GET /v1/uploads/{id}` lists received and missing parts. | `uploads.py:205` |
| Complete | `POST /v1/uploads/{id}/complete` with `sha256`. Refuses if a part is missing (409) or the hash differs from the one given at start (422). Status becomes `finalizing`; a `finalize_upload` job is queued. | `uploads.py:402-419` |
| Finalise | The worker re-reads every part, re-checks each part's hash, the whole-file hash and the size, then writes the `app.documents` row. The parts stay where they are: the document's storage path is the upload folder. | `api/app/core/uploads.py:78-205` |
| Handoff | The same transaction publishes `DocumentUploaded`; the pipeline queues `scan_document`. Job payloads carry ids only. | `api/app/features/pipeline/__init__.py:72-77` |
| Scan | The whole file is streamed to the scanner. A clean records-kind document is then split into pages. | `api/app/features/pipeline/service.py:130-151` |
| Failure | A hash or size mismatch sets `failed` with a reason. Cancel sets `cancelled`. | `uploads.py:421-439` |
| Clean-up | A daily job expires old uploads and deletes the parts of expired, cancelled and failed uploads. A second job removes orphaned upload folders. | `api/app/core/housekeeping.py:38-151` |

Allowed status changes are fixed in the database: `open` → `finalizing`, `cancelled`, `expired`; `finalizing` → `complete`, `open`, `failed`, `expired` (`supabase/migrations/0125`).

Access is re-checked on every call, by the route and again by row-level security. No temporary upload permission exists: the browser never talks to storage.

Leftover lifetime: an abandoned open upload keeps its sealed parts for up to about 8 days; a cancelled or failed one for up to about 1 day.

## 2. File validation rules

| Check | Rule found | Result |
|---|---|---|
| Size | 1 byte to 500 MB per file, at start | Enforced |
| Open uploads | 20 per person | Enforced |
| Total per case | None | Weakness U5 |
| File name | 1–255 characters, no ASCII control characters | Enforced; Unicode direction overrides accepted (V5b) |
| Kind | Must match a code pattern | Not checked against the case's packs (V6) |
| Declared type | Not accepted from the client at all | Sound |
| Extension | Ignored | Sound |
| Signature | Sniffed from the bytes at the split step: PDF, JPEG, PNG, TIFF, zip and Office files, email, WAV, AVI | Enforced; unknown types are kept as opaque exhibits |
| Virus scan | Whole file, before splitting | See V1, V2 |
| Locked PDF | Marked "locked", no pages | Enforced |
| Garbage PDF | Marked "damaged" | Enforced; a cut-off PDF is not (V4d) |
| Zero-page PDF | Accepted with no pages and no reason | Weakness V4e |
| Page count | None | Weakness V4b |
| Page size at render | None | Weakness V4c |
| Active content | Document-level actions and embedded files are dropped when a page is cut; page-level script and link actions remain | Info V4a |
| Zip | At most 2,000 members, 500 MB unpacked, depth 3, no locked members | Enforced per archive, not per upload (V3a) |
| Zip sizes | A forged small size in the zip header is caught | Enforced |
| Zip member names | Never used as a path or storage key | Sound; control characters kept in the stored name (V3c) |
| Polyglots | PDF+zip, PDF+HTML, PNG+zip: stored byte for byte, served as opaque data | Sound |
| Image bombs | The image library's pixel limit is left on | Enforced |

No uploaded file is ever converted or sanitised for download: the original is returned exactly as uploaded.

## 3. Storage access model

- **Provider:** AWS S3 through `boto3`, one bucket per cell (`api/app/core/storage.py`).
- **No direct access:** there are no signed or public URLs anywhere in the API. Every byte goes up and down through FastAPI. Tested: no upload or document response contains a storage path or link.
- **Object keys:** every key begins `cases/<case uuid>/`. Upload parts are `cases/<case>/uploads/<upload uuid>/<5-digit part>`. Keys contain UUIDs only, never a file name. Tested.
- **Isolation:** by case, through the key prefix, the per-case encryption key, and the access check on every route.
- **Server-side encryption:** every write asks for SSE-S3 (AES256). Tested against the stand-in.
- **Download:** `GET /v1/cases/{case}/documents/{doc}/file` needs `case.view_content`, sends `application/octet-stream`, `nosniff` and `no-store`, and stops the stream with an error if the bytes no longer match the recorded hash. No `Content-Disposition` is sent (S3, low).
- **Bucket settings:** a one-off script sets public access block, default encryption, a deny-non-TLS policy, a 1-day multipart abort rule and versioning off (`infra/s3/create-bucket.sh`). Nothing in code enforces these. Unverified against the real bucket.
- **Temporary files:** spool files are created unlinked with mode 0600. The split step writes a named plaintext temp file and removes it; tested after success and after two kinds of failure. A killed worker leaves it on the container's disk.

## 4. Cryptographic implementation

| Item | Finding | Where |
|---|---|---|
| Algorithm | AES-256-GCM (`cryptography` library) | `api/app/core/crypto.py:33-42` |
| Nonce | 12 random bytes per seal; 5,000 seals gave 5,000 distinct nonces | `crypto.py:169` |
| Blob | Version byte `0x01`, nonce, ciphertext with tag | `crypto.py:170` |
| Tag check | Any single changed byte gives `CiphertextInvalid`; truncated and empty blobs refused | Tested |
| Associated data | Case id and purpose. For stored objects the purpose is the object path | `crypto.py:258-261` |
| Binding | A blob does not open under another case or another path. Swapping two cases' wrapped keys in the database does not help: the wrap is bound to the case id | Tested |
| Key hierarchy | One master key wraps a random 32-byte key per case, stored in `app.case_keys` | `crypto.py:133-137`, `0006` migration |
| Key separation | Two cases get different keys | Tested |
| Master key | `KEY_PROVIDER=local`, key in an environment variable. A wrong master key cannot unwrap | `crypto.py:152-165` |
| KMS | Not implemented; any other provider value is refused at start | Tested, pinned |
| Rotation | None. No re-wrap, one fixed blob version | Code reading |
| Failure | If the key provider is unavailable, work pauses. Nothing is ever written unsealed | Tested |
| Destruction | After `destroy_case_key`, a new process cannot decrypt; a process that received the notice refuses at once | Tested |
| Stale cache | A process that missed the notice can decrypt for up to 30 seconds | POLICY C1c |

What is sealed at application level: every stored object (upload parts, page images, thumbnails, page PDFs, exports, transcripts, reports), OCR words, opinion text, notes, corrections, party names and dates of birth, and page extraction results.

What is not sealed at application level: `facts.value`, `facts.fields`, page embeddings, page fingerprints and printed dates. These rely on database encryption at rest and are removed by the row purge, not by key destruction.

Transit: storage, OCR and AI endpoints must be HTTPS or WSS on an allow-list. The database connection's TLS mode comes from the connection string the operator supplies; the code does not require it.

## 5. Integrity controls

All of these were tested and held:

- A part with a wrong checksum, wrong length or out-of-range number is refused.
- Completion is refused while a part is missing, and when the final hash differs from the hash given at start.
- A sealed part copied from another upload, in the same case or another, makes finalise fail: the path is part of the seal.
- Two parts swapped in storage, a deleted part, a flipped byte, or a part re-sealed with other content are each detected. The download never returns a clean 200 with wrong content.
- A part sent after completion, after cancel or after expiry is refused and the stored object is unchanged.
- Completing twice gives one document. Replaying an identical part is harmless.
- Changing the recorded hashes in the database makes finalise or download fail, not succeed.

Limits of the control: the whole-file hash is checked at finalise and on download, but not when a page image is read. Page images are protected by the seal only. Pages, OCR words and facts carry no hash of their source, and a re-run of the page job overwrites the page objects in place (I1, policy).

## 6. Deletion lifecycle

Deletion of a case is asynchronous and both physical and cryptographic. The `delete_case` job (`api/app/features/close/jobs.py:187-260`) runs three steps in order:

1. Delete every object under the case's prefix.
2. Destroy the case key.
3. Purge the rows and write the certificate, in one transaction.

| Copy | After deletion | Evidence |
|---|---|---|
| Stored objects under the case prefix, including open and cancelled upload parts | Gone | Tested, bucket-wide scan |
| Objects outside the case prefix | None found | Tested |
| Case key | Wrapped key null; a blob saved beforehand cannot be opened | Tested |
| Database rows | No marker text left in any table of `app` or the queue | Tested |
| Kept by design | Case tombstone, key row, audit log (ids only), deletion ledger, certificate, bundle page count | Tested |
| Queue rows | Keep the case id and job ids for ever; no content | D1f, info |
| Certificate and email | Hold no case content | Tested |
| Access | Every content route answers 404 for all roles; the certificate answers for the expert and organisation admin only | Tested |
| Uploads after deletion | Refused; no object created; a leftover finalise job creates nothing | Tested |
| Storage outage during deletion | Nothing is destroyed; the retry finishes with one certificate | Tested |
| Backups | A restored deleted case is purged again by the ledger replay | Existing guard test |

Not removed by deletion, by code reading:

- **Azure Document Intelligence:** results are fetched by polling; no delete call is made. Retention is Azure's.
- **AI provider:** request retention is the provider's.
- **Logs and Sentry:** scrubbed at creation; nothing deletes them.
- **Database backups:** retained for 30 days (`close/jobs.py:45`). Plaintext columns such as `facts.value` remain readable in a backup until it expires.
- **Killed worker:** a named plaintext temp file can remain on the container's disk.

## 7. Test matrix

| Area | Cell | Result |
|---|---|---|
| Authorisation | No session on start, part, status, complete, cancel | Pass (401) |
| | Forged or random upload id | Pass (404) |
| | Outsider on someone's upload id | Pass (404) |
| | Role without `case.upload` | Pass |
| | Member removed mid-upload: next part and complete | Pass (refused) |
| | Colleague acts on another member's upload | POLICY U1b |
| | Document of case B through case A's path | Pass (404) |
| Integrity | Wrong checksum, length, part number | Pass |
| | Complete before all parts; wrong final hash | Pass |
| | Part copied across uploads or cases | Pass |
| | Parts swapped, deleted, altered in storage | Pass |
| | Part after complete, cancel, expiry | Pass |
| | Complete twice; replayed part | Pass |
| | Tampered hashes in the database | Pass |
| Limits | Empty file, over 500 MB, 21st open upload | Pass |
| | Total claimed per case | Fail U5 (low) |
| Names and kind | Path characters, quotes, 255 multibyte characters | Pass |
| | Control characters, over-long, empty | Pass (refused) |
| | Direction-override characters | Fail V5b (low) |
| | Kind outside the case's packs | Fail V6 (low) |
| Scanner | Settings choose clamd; nonsense refused | Pass |
| | Scanner unreachable or unclear verdict: nothing recorded | Pass |
| | Stream boundaries, reply shapes | Pass |
| | Cell with no scanner set | Fail V1 (medium) |
| | Stand-in selectable in a cell | POLICY V1b |
| | Readiness does not ping the scanner | POLICY V1c |
| Scan gating | Flagged file never split | Pass |
| | Flagged parent archive: nothing unpacked | Pass |
| | Flagged file downloadable | Fail V2b (medium) |
| | Unscanned file downloadable | Fail V2b2 (medium) |
| | Expert told the file was blocked | Fail V2c (low) |
| | Export leaves out flagged files | Fail V2d (latent, low) |
| | Flagged or unscanned letter read | Fail V2e, V2e2 (low) |
| | Archive member scanned on its own | Fail V2g (low) |
| Archives | Member count, size, forged size, locked, depth, traversal names | Pass |
| | Total across nested archives | Fail V3a (medium) |
| | Member name with a control character | Fail V3c (low) |
| Documents | Intact, locked, garbage, wrong-type, polyglots | Pass |
| | Image pixel bomb | Pass |
| | Cut-off PDF | Fail V4d, V4d2 (medium) |
| | Zero-page PDF | Fail V4e (medium) |
| | Over 4,000 pages, PDF and TIFF | Fail V4b, V4b2 (low) |
| | Huge page at render | Fail V4c (medium) |
| | Script and links in stored page PDF | Fail V4a (info) |
| Storage | Key pattern, SSE, sealed bytes, no links in responses | Pass |
| | Download equals upload; headers | Pass; S3 (low) |
| | Leftovers of cancelled, failed, expired uploads | Pass |
| | Split temp files after success and failure | Pass |
| Crypto | Nonce, tag, binding, key separation, wrong master key | Pass |
| | Destroyed key, fresh and notified process | Pass |
| | Destroyed key, process that missed the notice | POLICY C1c |
| Deletion | Objects, key, rows, certificate, access | Pass |
| | Queue rows | Fail D1f (info) |
| | Storage outage | Pass |
| | Partial storage failure | Fail D2b (medium) |
| | Access while deletion is queued | POLICY D3 |
| | Upload racing a deletion | Fail D4b, D4d (low) |
| | Remove one document | Fail D6a (low) |
| | Export of a real upload | Fail D6c (high) |
| | Export is one-time and its copy goes | Pass |

## 8. Confirmed weaknesses

Each was reproduced by a test and checked against the code by the reviewer. Line numbers are at `1cadc5a5`.

### High

**D6c. The expert's full export fails for real uploads.**
Request: upload a document through the API, finalise it, run the `export_case` job with the full variant. Outcome: the job fails with a missing-object error. Cause: the export reads a document's storage path as one object, but an uploaded document's path is a folder of parts. `api/app/features/close/jobs.py:392-398`. The existing export tests seed a single object, so they pass. This is the expert's way to take their records before a case is deleted.

### Medium

**V2b, V2b2. A flagged or unscanned original is served.**
Request: `GET /v1/cases/{case}/documents/{doc}/file` by a case member, for a document whose scan row says infected, or which has no scan row yet. Outcome: 200 with the decrypted file. The route has no scan check. `api/app/core/routes/uploads.py:443-497`. Needs a case member to upload the file; the harm is to colleagues who download it.

**V1. A cell with no scanner configured runs the stand-in.**
Input: `VIRUS_SCANNER` and `CLAMD_HOST` both unset. Outcome: the worker builds the test stand-in, which flags only the EICAR test string, and records every file as clean. No error or warning. `api/app/core/scan.py:109-116`, `api/app/features/pipeline/wiring.py:99`.

**V3a. Unpack limits apply per archive, not per upload.**
Input: one zip holding five zips, each unpacking to 90% of the limit. Outcome: 4.5 times the limit is unpacked and stored. At the real limits one upload can unpack to many times 500 MB. `api/app/features/pipeline/convert.py:143-153`, `service.py:193-211`.

**V4c. No pixel cap when a page is rendered.**
Input: a one-page PDF with a 200 by 200 inch page. Outcome: the renderer is asked for about 900 million pixels, about 3.6 GB. The worker would be killed and the job retried. `api/app/features/pipeline/render.py:22-31`.

**V4d, V4d2, V4e. Damaged PDFs lose pages without a sign.**
Input: a 3-page PDF cut off at 30% or 45% of its bytes; a PDF with no pages. Outcome: the first is numbered as 2 pages; the second as 3 pages with empty content; the third vanishes. None is marked damaged. `api/app/features/pipeline/service.py:173-225`. This undermines "the expert reads every page".

**D2b. A partial storage delete is taken for success.**
Setup: storage refuses to delete one object and deletes the rest. Outcome: the deletion job finishes, destroys the key and issues the certificate, with one object left. The delete call never reads the errors storage returns. `api/app/core/storage.py:371-384`. The leftover is sealed under a destroyed key, so it cannot be read; the certificate's statement is what is wrong.

### Low

| Id | What happens | Where |
|---|---|---|
| V2c | After an infected upload the expert sees a document with no pages and no reason; only an audit row records it | `pipeline/service.py:146` |
| V2e, V2e2 | A flagged or unscanned instruction letter is still sent to OCR and the AI | `setup/jobs.py:72-95` |
| V2d | The export does not filter on the scan verdict. Unreachable until D6c is fixed | `close/jobs.py:343-398` |
| V2g | A file unpacked from a zip or email is recorded clean without its own scan. Safety rests on the scanner having looked inside the parent, which ClamAV does | `pipeline/repo.py:285-289` |
| V3c | A zip member's name keeps control characters that the upload route refuses | `pipeline/repo.py:276-283` |
| V4b, V4b2 | No page limit: a 4,001-page PDF or TIFF is accepted | `pipeline/service.py:173-180`, `convert.py:262-271` |
| V5b | File names with direction-override characters are accepted, so a name can display reversed | `core/routes/uploads.py:53` |
| V6 | A kind that is not in the case's packs is accepted | `core/routes/uploads.py:61` |
| U5 | No cap on the total size a case's open uploads may claim | `core/routes/uploads.py:176-186` |
| S3 | The original is sent without `Content-Disposition: attachment` | `core/routes/uploads.py:497` |
| D4b | A part in flight when a deletion runs is refused, but its object stays under the deleted case's prefix, sealed under the destroyed key | `core/routes/uploads.py:292` |
| D4d | A part sent between a failed deletion step and its retry survives the deletion, sealed under the destroyed key | `close/jobs.py:206` |
| D6a | Removing one document does nothing and answers 404: the API role has no delete policy on documents. Fails closed | `0042` migration `:36-42`, `pipeline/repo.py:1239` |

### Information

| Id | What happens | Where |
|---|---|---|
| V4a | Stored page PDFs keep page-level script and link actions. No route sends a page PDF to a browser | `core/pdf.py:39-47` |
| D1f | Queue rows keep the case id and job ids after deletion; no content | `core/jobs.py` |

## 9. Policy items for decision

The written spec allows or is silent on each of these. The tests are marked `AUDIT-12 POLICY`.

| Id | Behaviour | Question |
|---|---|---|
| U1b | Any case member who may upload can send parts into, complete or cancel a colleague's open upload | Tie an upload to the person who started it? |
| V1b | The stand-in scanner can be selected by setting in any cell (ADR 0027) | Refuse it in a live cell? |
| V1c | Nothing checks that the scanner answers before the worker takes work. It fails closed: files wait | Add a readiness check? |
| C1c | A process that missed the key-destroyed notice can decrypt for up to 30 seconds | Acceptable window? |
| D3 | While a deletion is queued and not yet run, files, new uploads and exports still work | Block content once deletion is requested? |
| I1 | Page images are overwritten by a re-run and carry no hash of their source (code reading, no test) | Record a source hash on pages? |

## 10. Unverified

| Item | What would settle it |
|---|---|
| Real bucket: public access block, policy, versioning, replication, lifecycle | `aws s3api get-public-access-block`, `get-bucket-policy`, `get-bucket-versioning`, `get-bucket-replication` on the live bucket |
| IAM: the app's role is limited to this bucket | The attached policy in the AWS account |
| Production `CLAMD_HOST`, `VIRUS_SCANNER`, `KEY_PROVIDER` | The Railway variables for the API and both workers |
| Where the master key is held and who can read it | Railway access list and variable history |
| Database TLS mode in production | The `sslmode` in the live connection string |
| Any request-size limit in front of the API | Railway or proxy settings; the app has none of its own beyond the 8 MiB part check |
| Database backup retention and who can restore | Supabase project settings |
| Azure Document Intelligence result retention | Azure resource settings and the service's documented retention |
| AI provider request retention | The provider agreement in force |
| ClamAV really inspects inside archives and emails at our sizes | A live clamd test with an EICAR file inside a zip and an email |
| Temp files after a killed worker | A kill test on a deployed worker |
| Quarantine purge, transcript removal, offline-pack delete each remove both object and row | Tests not written in this step |

## 11. Remediation, in priority order

1. **D6c:** make the export read each document through the same folder reader the download uses, and leave out documents that are not scanned clean (closes V2d).
2. **V2b, V2b2:** refuse the original-file download unless the scan row says clean.
3. **V1:** refuse to build the stand-in scanner unless it is asked for by name.
4. **D2b:** read the errors from the storage delete call and fail the step, so the job retries.
5. **V3a, V4c:** one unpack budget per upload; a pixel cap before rendering.
6. **V4d, V4e:** mark a PDF damaged when the reader had to repair it or found no pages.
7. **V2e, V2c:** join the scan verdict before a letter is read; tell the expert when a file is blocked.
8. **Key management:** implement the KMS provider and a re-wrap path for the master key.
9. **D4b, D4d:** take the lock before writing a part, and re-list storage on a deletion retry.
10. **V4b, V5b, V6, V3c, U5, S3, D6a:** page limit, name rules, kind check, per-case cap, attachment header, document delete policy.
11. **D1f:** remove finished queue rows for a deleted case.

### Fixed on this branch

Items 1 to 5 are fixed, and S3 from item 10 is also fixed. Each fix turned its strict xfail test green, and the marker was removed:

- **D6c, V2d:** the export reads each document part by part and checks its SHA-256. A file not scanned clean is left out and listed in the manifest's `left_out`, as `scan_pending` or `blocked`.
- **V2b, V2b2:** the original-file download returns 423 `file_not_ready` when the file has no scan verdict, and 409 `file_blocked` when it was flagged.
- **S3:** the original-file download sends `Content-Disposition: attachment; filename="document"`.
- **V1:** with no scanner configured, the API and the worker refuse to start. They also refuse to start when `VIRUS_SCANNER=recorded` is set together with `CLAMD_HOST`. A deployed cell must set `CLAMD_HOST` on the `api`, `worker` and `worker-interactive` services.
- **D2b:** a storage delete that leaves objects behind raises `StorageError`, so the step retries. The quarantine purge carries on past a single stuck item.
- **V3a, V4c:** one unpack budget now covers each upload. It is tracked in the new column `document_parts.size` (migration `0143`). Page pictures are capped at 25 million pixels. Two renderers are still not capped: `api/app/core/pdf.py:60` and `api/app/features/report/pdf_out.py:50`.

Items 6 to 11, apart from S3, are open.
