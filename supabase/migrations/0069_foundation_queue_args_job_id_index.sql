-- 0069 foundation: an index on the job id inside the queue payload (audit 15, BG-13).
--
-- The orphan sweep (`app.requeue_orphaned_jobs`), the cancel and retry paths and the support actions look a queue row
-- up by `args ->> 'job_id'`. Without an index each lookup scans the whole queue table (about ten seconds on a
-- 25,000-row backlog). An expression index serves them all; the table is also pruned (0071).

set role app_migrator;

create index if not exists procrastinate_jobs_args_job_id_idx
  on procrastinate.procrastinate_jobs ((args ->> 'job_id'));

reset role;
