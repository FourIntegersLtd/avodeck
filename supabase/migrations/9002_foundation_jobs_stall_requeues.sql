-- 9002 foundation: a job's stall requeues are counted, so they can be capped.
--
-- A stall (the worker's heartbeat stopped) puts a job back without counting an attempt (0058). A job that
-- stalls every run (a memory kill each time) would loop for ever. `stall_requeues` counts the times the
-- worker's sweep put the job back after a stall; after `JOB_MAX_STALL_REQUEUES` (default 5) the next stall
-- ends the job `failed` with the code `stalled_too_often` through the dead-letter path instead (F6).
-- Nothing else reads the column. It sits in the 9000 range only because 0057-0099 are used or reserved.

set role app_migrator;

alter table app.jobs add column stall_requeues integer not null default 0 check (stall_requeues >= 0);

reset role;
