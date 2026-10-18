# 0015 Audit rows are kept unchanged when a case is deleted

Date: 2026-10-17 · Status: accepted

**Problem.** Architecture 10 says that when a case is deleted its audit rows are "kept as pseudonymised metadata". Slice H asked whether that needs an extra step (rewriting or blanking columns) at deletion.

**Decision.**
- `app.purge_case()` leaves `audit_log` rows exactly as they are. No extra pseudonymising step.
- An audit row holds only ids (person, organisation, case, target such as `page:<uuid>`), a short action code, a time and the chain hashes; never page text, names, identifier numbers or dates of birth (rule 6, enforced when rows are written).
- Once the case is deleted, the ids of its data point at nothing: the rows they named are gone and the case row is a tombstone without a label. Ids that lead to no content count as pseudonymised metadata.
- Rewriting the rows would also break the audit hash chain, which covers every column.

**Consequences.**
- The deletion certificate lists the chain heads of the deleted `page_reads` and `fact_verifications`; the audit chain itself stays verifiable after deletion.
- Person ids in audit rows still identify the expert or a colleague (not the patient); they follow the person's own account retention, not the case's.
- If a future audit action needs a free-text field, it must not hold patient data; otherwise this decision has to be revisited.
