# 0030 Upload finalisation runs as a worker job

Date: 2026-10-31 · Status: accepted · Decided by: the founder (upload hardening, stream A)

**Problem.** `POST /uploads/{id}/complete` re-read, decrypted and hashed every part inside the request. For a 500 MB bundle that outlives the 60 second proxy timeout, ties up an API connection and loses the work if the client disconnects.

**Decision.**
- `complete` is a claim. Under the per-upload advisory lock it moves `open` to `finalizing`, enqueues one `finalize_upload` job (`core/uploads.py`, interactive, does not change the case) and returns 202. A repeat while the job is queued or running returns 202; after success it returns the document (200); with a failed or absent job it re-claims with a fresh job.
- The job streams parts through `StoredDocument.chunks` (at most two parts in flight), checking each part's hash, the whole-file hash and the byte count. Success inserts the document, completes the upload, audits `document.uploaded` and publishes `DocumentUploaded` in one transaction. A missing part reopens the upload; Storage errors retry the job; a damaged part or mismatch fails the upload for good.
- The database guard (migration 0124) owns the legal transitions; the job loses a race harmlessly (zero rows updated rolls back).

**Consequences.** The client polls the upload (or waits for `DocumentUploaded` downstream) instead of getting the document at once. The job's read transaction is held open while it streams; the worker holds plaintext only in memory. Crypto-erasure claims are qualified accordingly (ADR 0010, threat model T11, T17).
