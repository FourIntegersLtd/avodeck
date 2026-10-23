# Slice H: Close the case and compliance

Owner: Claude session, branch `slice/h-close` · Phase: MVP · Migrations 0800–0899

## Goal
When a case ends, the expert downloads their record of work, chooses how long records are kept, and Avodeck deletes everything on schedule and issues a certificate of deletion to the expert and the instructing party.

## Screens
- 26 Close the case (`S26-Close`)

## Endpoints (architecture 5.2)
- `POST /v1/cases/{id}/settle`
- `POST /v1/cases/{id}/export` 🔒 `{files?: [reports|method|reads|invoice]}` → 202; `GET /v1/cases/{id}/export/files` (kinds, counts, default ticks); `GET /v1/cases/{id}/export/{job_id}/download` 🔒
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
- [x] Export, retention change and deletion refuse without step-up (403 `step_up_required`)
- [ ] Export ZIP contains every report version with matching SHA-256, the methodology log, the page-read log and the invoice; download works once and is audited (built: report versions, hashes, requests.csv, logs/page-reads.csv, logs/methodology.csv, invoices.csv)
- [x] `keep_default` sets `delete_after` to six years after settlement; `delete_now` schedules deletion today
- [x] Deletion removes every row for the case from every table listed in `case_tables.py`, and every Storage object under the case prefix (verified by listing)
- [x] A new case table added without registering in `case_tables.py` fails a core test
- [x] After deletion, the case key is destroyed and any remaining backup blob cannot be decrypted
- [x] Certificate lists counts, hashes, deletion time and the date backups expire; it is emailed to the expert and the current instructing parties with no patient details (role codes `named_expert`, `instructing_party_<n>`; migration 0804; JSON or PDF)
- [x] Archive and reopen change status and recalculate `delete_after`
- [x] Deletion job is idempotent and resumes safely if interrupted
- [x] Screen 26 passes Playwright + axe and matches its canvas board

## Behaviour rules this slice must build (architecture 10, 5.4)

These came out of the behaviour audit and are in scope. Each bullet is an acceptance test to write first. Permissions come from `docs/spec/permissions.md`; scenarios from `docs/spec/scenarios.md`.

**Extra tables owned:** `legal_holds`, `deletion_ledger` (writes).

**Extra endpoints:** `POST /v1/cases/{id}/legal-holds` 🔒, `POST /v1/cases/{id}/legal-holds/{hold_id}/release` 🔒, `GET /v1/cases/{id}/audit`.

- [x] Deletion skips a case with an active legal hold and notifies the named expert (S13, F30)
- [x] Every deleted case ID goes in `deletion_ledger`; the restore runbook replays it before reopening (S14, F29)
- [x] On deletion, audit rows are kept as pseudonymised metadata; `page_reads` and `fact_verifications` go, with their chain heads on the certificate (audit rows stay unchanged: ids and short codes only, pointing at nothing once the case is gone; ADR 0015)
- [x] Quarantined pages, superseded pages and exhibits are included in deletion
- [x] Organisation admins get an administrative export (no page images); the named expert gets the full export
- [x] Withdrawn cases follow the same retention as settled ones

## Rules that apply
4, 5, 6, 9, 10 from the root `AGENTS.md`.

## Out of scope
Producing the content being exported (other slices), invoice generation (F).

## As built (scope notes)
- `POST /settle` takes no body; the database sets `settled_at`. `GET /close` returns the screen's summary.
- The certificate is JSON (counts, hashes, log chain heads, dates, recipient role codes), or a PDF of the same values with `?format=pdf` or `Accept: application/pdf` (`app/core/pdf_document.py`; words in `features/close/words/`).
- Backups are assumed gone 30 days after deletion (`BACKUP_RETENTION_DAYS` in `features/close/jobs.py`); settled or withdrawn cases archive after 30 days.
- The export holds `requests.csv` (core/contracts `logged_requests`), `logs/page-reads.csv` (`page_read_log`: seq, prev_hash, hash) and `logs/methodology.csv` (`verification_counts`). `invoices.csv` comes from `core/contracts/invoices.case_invoice_rows` (slice F's sent invoices with payments and credits; never drafts).
- A deleted case answers only `deletion-certificate` (foundation 0018; the guard `test_only_the_certificate_passes_on_a_deleted_case` forbids widening it). `GET /close` is 404 there by decision, and S26 shows the certificate on its own when the case answers 404. The strict xfail `test_a_deleted_case_still_shows_its_close_summary` records that decision.
- The certificate email goes to the named expert and each current instructing party with an email (`app.close_certificate_recipients`, 0804); org-admin recipients do not exist yet. `sent_to` keeps role codes only, so a retry never emails anyone twice.
- A withdrawn case's retention counts from the day it was withdrawn: `cases.closed_at` (foundation 0022), read through `app.close_withdrawn_on` (0803 replaced H's audit-row trigger).
- Reopening is refused (409) once `delete_after` has come. Exports are refused once deletion has started; the export job holds a share lock on the case so a deletion run starts either before it (nothing written) or after it (its Storage sweep removes the copies).
- Export copies nobody downloaded are removed by the daily run `EXPORT_KEEP_DAYS` (7) days after they were made. Only the requester's copy is built (`jobs.requested_by`, foundation 0022): `full` for the named expert, `admin` for an organisation admin; only the requester may download it (404 for anyone else).
- The download streams in 1 MB pieces; `CaseCrypto` opens a file whole, so the decrypted ZIP is held in memory once. The worker builds each ZIP in a temporary file.
- `GET /close` shows `deletion_started` to every case member (`app.close_deletion_started`); the certificate itself stays `case.view_certificate`.
- S26 is mounted by `app/capabilityRoutes.ts` from `closeRoutes`; its words are registered by the capability (`words.ts`). Its Playwright check (axe, 52px buttons, 17px text) runs against a stubbed settled case.

- Export file choice (S26 checkboxes, "Download N files"): jobs carry no payload and the jobs table is foundation-owned, so the chosen kinds are written as an encrypted note at `<case prefix>/exports/<job>/selection.json` when the export is requested; `export_case` reads and removes it (a deletion run sweeps it if the job never runs). No selection means every kind. `reports` = report versions (and the bundle for the named expert); `method` = requests.csv and logs/methodology.csv; `reads` = logs/page-reads.csv; `invoice` = invoices.csv. `hashes.csv` and `manifest.json` are always included. The invoice is unticked by default (as on the board). Foundation need, if wanted later: a payload column on `jobs`.

## Definition of done
Root checklist, plus: a synthetic case can be exported and fully deleted on staging with a certificate produced.

## Organisation retention limit (cross-slice request O to H)
- `GET /cases/{id}/close` carries `max_retention_years` (the organisation's `policy.max_retention_years`, read with `core/contracts/org_policy.py`; null when none).
- `PUT /retention` with `keep_default` is refused (422 `retention_exceeds_org_policy`) when the pack's years are longer than the limit. `return_then_delete` and `delete_now` are never longer. Settling, archiving and the daily dating run never date a case later than the limit.
- S26 turns the `keep_default` choice off and says why (`close.keep.limited`).
- Account closure: `core/contracts/close.py` `request_case_deletion` queues H's `delete_case`; `test_account_closure_deletion_reaches_the_delete_case_job_end_to_end` runs that job and checks the rows are gone and the certificate written.
