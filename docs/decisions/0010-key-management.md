# 0010 Per-case encryption keys held in AWS KMS (London)

Date: 2026-10-16 · Status: accepted for design; confirm with spike 8 before stage 2. Foundation stage 1 uses a local key provider behind the same `core/crypto.py` interface

**Decision.** Page images, OCR text, transcripts and generated documents are encrypted per case with a data key (AES-256-GCM, envelope encryption).
- Each case's data key is wrapped by a customer master key in **AWS KMS, eu-west-2 (London)**. We already have an AWS account for Bedrock London.
- Only the wrapped key is stored, in `case_keys` (case_id, wrapped_key, kms_key_arn, created_at, destroyed_at).
- Unwrapped keys are cached in worker and API memory for at most 5 minutes and never logged.
- **Deletion** destroys the wrapped key row and logs the destruction. Any copy left in backups can no longer be decrypted once the key is gone.
- Each cell uses its own KMS key in its own region (Australia: ap-southeast-2; Canada: ca-central-1).

**Options considered.**

| Option | For | Against |
|---|---|---|
| AWS KMS London (chosen) | Account already exists; FIPS-validated HSMs; IAM policies; audit via CloudTrail; per-region keys for future cells | One more provider in the request path (cached keys keep latency low) |
| Azure Key Vault UK South | Already using Azure | Second key system for future cells; we use Azure only through stateless APIs today |
| Supabase Vault (pgsodium) | No extra service | Keys live next to the data they protect, which defeats the insider threat (T11) and makes crypto-shredding weaker |
| DigitalOcean | — | No key management service |

**Consequences.**
- `core/crypto.py` (foundation) provides `encrypt(case_id, bytes)` and `decrypt(case_id, bytes)`. Slices never handle keys.
- IAM: App Platform can't assume IAM roles, so the API, worker and deletion job each have their own IAM user with access keys (architecture 9). The API and worker users may Encrypt, Decrypt and GenerateDataKey on the cell key only. Only the deletion job user can schedule key material changes. Policies require `aws:RequestedRegion = eu-west-2`. Keys rotate every 90 days.
- Supabase Storage objects are stored already encrypted. Supabase's own encryption at rest still applies underneath.
- If KMS is unreachable, processing pauses (fail closed); reading already-cached cases continues until the cache expires.
