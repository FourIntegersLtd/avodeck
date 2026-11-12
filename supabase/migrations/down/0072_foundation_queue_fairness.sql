-- Undo 0072: the queue is first in, first out within a priority again.
set role app_migrator;

drop trigger if exists procrastinate_jobs_fairness_priority on procrastinate.procrastinate_jobs;
drop function if exists app.queue_fairness_priority();
drop index if exists procrastinate.procrastinate_jobs_case_live_idx;

reset role;
