# ADR 0032 — Case files on S3

**Status:** Accepted  
**Date:** 2026-10-31

## Context

Avodeck's case files (upload chunks, page images, one-page PDFs, built reports, invoices,
transcripts, export ZIPs, trial packs, templates and signatures) were stored in a private
Supabase Storage bucket (`case-files`), reached only by FastAPI (rule 4). AWS Bedrock already
runs in `eu-west-2` (London) under a scoped IAM user. Moving files to S3 in the same region
and account keeps files and models in one cell, simplifies credentials (one IAM user for both),
and removes the dependency on Supabase for anything other than Postgres, Auth and the job queue.

osceguide already uses S3 via boto3 in `eu-west-2` (`api/app/features/media_production/s3.py`),
giving a proven shape to follow — minus presigned URLs, which rule 4 forbids.

## Decision

Replace Supabase Storage with AWS S3 as the object store for all case files and person-level
files (templates, signatures). Everything else stays on Supabase: Postgres + pgvector, Auth
and the Postgres-backed job queue.

### Key points

- **One adapter, renamed.** `SupabaseStorage` → `CaseFileStorage` in `api/app/core/storage.py`,
  same nine-method surface (`ensure_bucket`, `put`, `get`, `list`, `list_folders`,
  `list_object_times`, `list_recursive`, `delete`, `aclose`), same `StorageError` /
  `StorageNotFound` exceptions. No protocol, no second implementation.

- **Storage keys unchanged.** `cases/{case_id}/…` and `people/{person}/…` are the same, so no
  migration, no column change, no feature-slice code change beyond the class rename.

- **No bucket versioning.** Immutability of served reports is enforced by the locked
  `report_versions` row in Postgres (SQLSTATE `AVLCK` → 409). Versioning would cause slice H's
  certificate-of-deletion to lie: "deleted" objects would linger as non-current versions.

- **No presigned or public object URLs.** Rule 4: the browser talks only to FastAPI. Downloads
  stream through FastAPI with `Cache-Control: private, no-store`. A guard test
  (`test_foundation_storage_region.py`) greps `api/app/` for presigned-URL patterns and fails
  the build if one appears.

- **Region enforced by `AWS_ALLOWED_REGIONS`.** `storage_settings()` refuses an `AWS_REGION`
  not in the comma-separated allow-list, matching `AI_ENDPOINT_ALLOW_LIST`. `S3_ENDPOINT_URL`
  is accepted only when the host is `127.0.0.1`, `localhost` or `[::1]`.

- **Server-side encryption.** Every `put_object` sets `ServerSideEncryption=AES256` (SSE-S3).
  This is a second layer: callers already seal bytes with the case's envelope key
  (`core/crypto.py`) before handing them to the adapter.

- **`LastModified` vs `created_at`.** The only caller that uses object timestamps is
  `expire_uploads` (housekeeping), which sweeps upload chunks. Chunks are written once and
  never rewritten, so S3's `LastModified` equals Supabase's `created_at` for this use case.

## Consequences

- The Supabase service-role key is now used only for Auth admin calls (`core/auth/supabase.py`).
  It is still required at deploy time.
- The existing scoped IAM user gains `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on the
  bucket's objects and `s3:ListBucket`, `s3:HeadBucket` on the bucket itself.
- Tests use a `moto` (S3 mock server) instead of local Supabase Storage.
- The `case-files` bucket in the Supabase project can be emptied and removed after deploy.
