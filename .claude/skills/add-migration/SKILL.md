---
name: add-migration
description: Add a Supabase/Postgres migration for an Avodeck slice with the right number range, schema, RLS, append-only rules and a database test.
---

# Add a migration

1. **Number range** for your slice (root `CLAUDE.md`): foundation 0000–0099, A 0100–0199, B 0200–0299, C 0300–0399, D 0400–0499, E 0500–0599, F 0600–0699, G 0700–0799, H 0800–0899, I 0900–0999, O 1000–1099. File: `supabase/migrations/<NNNN>_<slice>_<what>.sql`.
2. **Test first:** a database test in `backend/tests/<slice>/test_<table>_db.py` (and pgTAP for RLS where useful) that fails before the migration exists.
3. **Tables** go in the `app` schema (not exposed to Supabase's Data API). Use UUID primary keys, `created_at timestamptz default now()`, and `case_id uuid not null references app.cases(id) on delete cascade` for case data.
4. **RLS:** `alter table … enable row level security;` with policies for `app_api` and `app_worker` that call `app.can_access_case(case_id, '<action>')` (ADR 0013). Add a pgTAP test that a person without access sees zero rows and the bare role sees nothing. Add it even though FastAPI uses the service role.
5. **Append-only tables** (logs, verifications, reads): revoke UPDATE and DELETE; add `prev_hash` and `hash` columns filled by the API.
6. **Only your slice's tables.** Need a column on another slice's table or a shared contract table? Ask the foundation owner.
7. **Reversible:** include a matching down migration in `supabase/migrations/down/`.
8. **Status columns** get a trigger that allows only the transitions in `docs/spec/state-machines.md`.
9. **Editable rows** get an `etag`/`updated_at` column for optimistic concurrency.
10. **Behaviour:** answer the ten questions in the `slice-brief` skill for the new table; add it to architecture section 4, `docs/spec/data-dictionary.md` (every field in plain English) and `docs/spec/behaviour-index.md`; make sure case deletion covers it.
11. Run `python scripts/check_spec.py`.
12. Run `supabase db reset` locally, then `make test-slice S=<slice>`.
