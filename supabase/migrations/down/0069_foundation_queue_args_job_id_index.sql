-- Undo 0069: drop the index; the lookups still work, only slower.
set role app_migrator;

drop index if exists procrastinate.procrastinate_jobs_args_job_id_idx;

reset role;
