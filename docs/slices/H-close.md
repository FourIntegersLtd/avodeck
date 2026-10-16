# Slice H: Close the case and compliance

Owner: Claude session, branch `slice/h-close` · Phase: MVP · Migrations 0800–0899

## Goal
When a case ends, the expert downloads their record of work, chooses how long records are kept, and Avodeck deletes everything on schedule and issues a certificate of deletion to the expert and the instructing party.

## Screens
- 26 Close the case (`S26-Close`)

## Endpoints (architecture 5.2)
- `POST /v1/cases/{id}/settle`
- `POST /v1/cases/{id}/export` 🔒 → 202; `GET /v1/cases/{id}/export/{job_id}/download` 🔒
- `PUT /v1/cases/{id}/retention` 🔒 `{policy: delete_now | keep_default | return_then_delete}`
- `POST /v1/cases/{id}/archive`; `POST /v1/cases/{id}/reopen`
- `GET /v1/cases/{id}/deletion-certificate`
- Scheduled job (daily): deletion run

## Tables owned
`deletion_certificates`. Writes `cases.retention_policy`, `cases.delete_after`, `cases.status`.

## Worker jobs
- `export_case`: ZIP of report versions with hashes, methodology log, page-read log (CSV), invoice, instructing-party requests; streamed once through the API.
- `delete_case`: delete Storage objects (records, page images, OCR JSON, exports, packages), rows in every case table, embeddings; destroy the per-case encryption key (crypto-shredding); write the certificate (counts, hashes, timestamps, backup expiry date); email expert and instructing party via Resend.

## AI tasks
None.

## Contracts
- **In:** foundation tables and storage; every slice's case tables (deletion must cover them; each slice registers its tables in `core/contracts/case_tables.py`).
- **Out:** deletion certificates; evidence for the DPIA.

## Acceptance tests (write first)
- [ ] Export, retention change and deletion refuse without step-up (403 `step_up_required`)
- [ ] Export ZIP contains every report version with matching SHA-256, the methodology log, the page-read log and the invoice; download works once and is audited
- [ ] `keep_default` sets `delete_after` to six years after settlement; `delete_now` schedules deletion today
- [ ] Deletion removes every row for the case from every table listed in `case_tables.py`, and every Storage object under the case prefix (verified by listing)
- [ ] A new case table added without registering in `case_tables.py` fails a core test
- [ ] After deletion, the case key is destroyed and any remaining backup blob cannot be decrypted
- [ ] Certificate lists counts, hashes, deletion time and the date backups expire; it is emailed to the expert and the instructing party with no patient details
- [ ] Archive and reopen change status and recalculate `delete_after`
- [ ] Deletion job is idempotent and resumes safely if interrupted
- [ ] Screen 26 passes Playwright + axe and matches its canvas board

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `legal_holds`, `deletion_ledger` (writes).

**Extra endpoints:** `POST /v1/cases/{id}/legal-holds` 🔒, `POST /v1/cases/{id}/legal-holds/{hold_id}/release` 🔒, `GET /v1/cases/{id}/audit`.

- [ ] Deletion skips a case with an active legal hold and notifies the named expert (S13, F30)
- [ ] Every deleted case ID goes in `deletion_ledger`; the restore runbook replays it before reopening (S14, F29)
- [ ] On deletion, audit rows are kept as pseudonymised metadata; `page_reads` and `fact_verifications` go, with their chain heads on the certificate
- [ ] Quarantined pages, superseded pages and exhibits are included in deletion
- [ ] Organisation admins get an administrative export (no page images); the named expert gets the full export
- [ ] Withdrawn cases follow the same retention as settled ones

## Rules that apply
4, 5, 6, 9, 10 from the root `AGENTS.md`.

## Out of scope
Producing the content being exported (other slices), invoice generation (F).

## Definition of done
Root checklist, plus: a synthetic case can be exported and fully deleted on staging with a certificate produced.
