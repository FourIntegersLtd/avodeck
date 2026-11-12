-- 0068 foundation: the API role can no longer insert a job row itself (audit 15, BG-03).
--
-- `app.enqueue_job()` (security definer) is the only way a job is made: it looks up the case action the kind needs
-- (`app.job_kind_actions`, rule 9), checks the person has it, and puts the row on the queue. Policy `jobs_add` let
-- `app_api` insert into `app.jobs` directly with only `case.upload`, so a role holding upload could make a job of
-- ANY kind (export_case, delete_case) and skip that check; the orphan sweep then queued it at the inserter's own
-- priority. Nothing in the API inserts a job any other way. The worker role is unchanged (`jobs_job`).

set role app_migrator;

drop policy if exists jobs_add on app.jobs;
revoke insert on app.jobs from app_api;

reset role;
