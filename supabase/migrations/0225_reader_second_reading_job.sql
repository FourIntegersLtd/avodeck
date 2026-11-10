-- 0225 reader: a second reading remembers which job was queued for it (audit step 14, R-E-2).
-- Asking again for a page whose turn and picture are unchanged answers with this job, never with another page's.

set role app_migrator;

alter table app.second_readings add column job_id uuid references app.jobs (id) on delete set null;

reset role;
