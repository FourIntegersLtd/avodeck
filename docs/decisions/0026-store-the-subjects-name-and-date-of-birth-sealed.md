# 0026 Store the subject's name and date of birth, sealed

Date: 2026-10-30 · Status: accepted · Decided by: the founder (plan "strip the patient's own name and date of birth")

**Problem.** Rule 7 strips identifiers before every model call, but the recognisers can only hide names and dates they are given. Nothing stored the subject's own name, other names or date of birth: `PUT /cases/{id}/subject-key` keeps only a keyed hash for the duplicate-case warning. So no AI task could hide them, and the seams that take names and dates of birth stayed empty.

**Decision.**
- Slice C stores them in `app.case_subject_identity` (migration 0396): `name`, `other_names` (a JSON list) and `date_of_birth` (an ISO date, read by code in the pack's date order), each sealed with the case key (`CaseCrypto`, as `case_parties.name`), under the purposes `subject_name`, `subject_other_names` and `subject_dob`.
- Writing is for the named expert only (`PUT /cases/{id}/subject/identity`, action `conflict.declare`, the named-expert-only action; row-level security repeats it). The API never returns the values: `GET /cases/{id}/subject` gains only `identity_recorded`, from `app.case_identity_recorded()`, which any case member may ask.
- Only the worker and AI paths open the values: the worker role can select inside a job for the case and cannot write. Foundation adds a read contract (`identity_for`) that merges them with the party names; the pipeline, understand, write, report, after-serving and trial slices then pass them to `pii.strip`.
- Deleting a case removes the row (purge order, migration 0397) and destroys the case key, so old backups cannot open it (crypto-shredding, as for every sealed value).
- Nothing is logged: the audit row `subject.identity_set` holds the case id only.

**Consequences.** One more stored identifier (threat-model T29), protected like party names. Cases made before this have no identity until the expert adds it; the case details screen asks for it with a banner. The date of birth row in `deferred.md` is closed here; wiring the consumers follows in foundation.
