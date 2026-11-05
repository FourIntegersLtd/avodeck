-- 0058 foundation: a job put back after its worker stalled is not a new attempt.
--
-- `jobs.attempts` counts failures and requeues. A worker that stalls (its heartbeat stopped: a deploy, a
-- restart, a memory kill) did not make the job fail, so the worker's sweep marks the job `requeued_stall`
-- and the next run starts without adding an attempt, exactly as a resume from a pause does. The flag is
-- cleared when the job starts. Nothing else reads it.

set role app_migrator;

alter table app.jobs add column requeued_stall boolean not null default false;

reset role;
