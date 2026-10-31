# 0030 Upload finalisation runs as a worker job

Date: 2026-10-31 · Status: accepted · Decided by: the founder (upload hardening, stream A)

**Problem.** `POST /uploads/{id}/complete` re-read, decrypted and hashed every part inside the request. For a 500 MB bundle that outlives the 60 second proxy timeout, ties up an API connection and loses the work if the client disconnects.

**Decision.**
- `complete` is a claim. Under the per-upload advisory lock it moves `open` to `finalizing`, enqueues one `finalize_upload` job (`core/uploads.py`, interactive, does not change the case) and returns 202. A repeat while the job is queued or running returns 202; after success it returns the document (200); with a failed or absent job it re-claims with a fresh job.
- The job streams parts through `StoredDocument.chunks` (at most two parts in flight), checking each part's hash, the whole-file hash and the byte count. Success inserts the document, completes the upload, audits `document.uploaded` and publishes `DocumentUploaded` in one transaction. A missing part reopens the upload; Storage errors retry the job; a damaged part or mismatch fails the upload for good.
- The database guard (migration 0124) owns the legal transitions; the job loses a race harmlessly (zero rows updated rolls back).

- On a case on hold the job waits with the other processing jobs (`runs_on_hold` stays false): finalising adds a document and starts the scan the hold queues. The daily sweep expires a finalizing upload past `expires_at` unless its job is running (migration 0125), so a hold cannot strand it.
- Migration 0125 tightens the guard: only the reopen may change `received` or `chunk_sha256`, and only by dropping entries. The guard still allows `open -> complete` and `open -> failed` (no code takes them) so that a branch from before 0124 keeps working.

**Known gaps.** The job does not re-check case access: access revoked between the claim and the job still yields a document (the window is short; `documents.uploaded_by` and the `upload.completed` audit row say who sent it). The sweep marks an upload cleaned once Storage lists nothing after its delete; a listing that is empty for another reason than "gone" cannot be told apart from it.

**Consequences.** The client polls the upload (or waits for `DocumentUploaded` downstream) instead of getting the document at once. The job's read transaction is held open while it streams; the worker holds plaintext only in memory. Crypto-erasure claims are qualified accordingly (ADR 0010, threat model T11, T17).

**Purge order.** `uploads.finalize_job_id` references `jobs`, so `uploads` must be purged just before `jobs`. This is set in migration 1015 (foundation), not 0124: 0499, 0599, 0699, 0799 and 0999 each replace `app.case_purge_order()` with a fixed list and would undo an earlier splice (1014 hit the same trap). Any later migration that writes a fixed purge list must keep `uploads` before `jobs`, or run a new splice after it. `test_foundation_case_purge.py` catches a miss.
