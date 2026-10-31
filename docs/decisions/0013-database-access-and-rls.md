# 0013 How FastAPI connects to the database, so row-level security really works

Date: 2026-10-16 · Status: accepted

**Problem.** Rule 9 says row-level security (RLS) repeats the access check in the database. But the service-role key bypasses RLS completely, so if FastAPI used it, RLS would do nothing.

**Decision.**
- FastAPI and the worker connect straight to Postgres (`DATABASE_URL`, pooled through Supavisor in transaction mode) as **restricted roles**, never as the service role:
  - `app_api`: for API requests. It has no `BYPASSRLS` and owns no tables.
  - `app_worker`: for background jobs. It has no `BYPASSRLS` either.
  - `app_migrator`: runs migrations only, in CI and deploys. It is never available to the running app.
- **Every request runs in one transaction** that starts with `SET LOCAL app.person_id = '<uuid>'`, `SET LOCAL app.org_ids = '<uuid,…>'` and `SET LOCAL app.request_id = …`, set by `core/db.py` from the verified session. RLS policies call `app.can_access_case(case_id, action)`, a `SECURITY DEFINER` SQL function that reads `case_members`, `memberships` and `cases.named_expert_id`. It mirrors `require_case_access` and is tested against the same permissions matrix.
- **Every job runs in a transaction** that starts with `SET LOCAL app.job_case_id = '<uuid>'`. Worker policies allow rows only for that case. Jobs that span cases (the deletion run, reminders) go through named `SECURITY DEFINER` functions that are audited.
- **The service-role key is used only for Supabase Auth admin calls (Storage moved to S3, ADR 0032).** Storage paths are opaque, and FastAPI always runs `require_case_access` before streaming a file.
- **Without the session settings, nothing is visible.** A query run with `app.person_id` unset returns zero rows, so a forgotten setting fails closed.

**Consequences.**
- `core/db.py` (foundation) owns connections and session variables. Slices never open their own connections.
- pgTAP tests check that each role sees exactly what the permissions matrix allows, and that the bare `app_api` role sees nothing.
- With transaction pooling, everything must be `SET LOCAL` inside the transaction, never session-level.
- The job queue (Procrastinate) uses `app_worker` on a direct (non-pooled) connection, because it needs LISTEN/NOTIFY.
