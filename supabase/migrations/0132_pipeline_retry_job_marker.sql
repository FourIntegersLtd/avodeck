-- 0132: which "Try these pages again" run took a page (slice A).
-- The progress bar counts only the pages the run took, not every page processed while it ran. A plain marker
-- (no foreign key: jobs are pruned, the marker is only compared with the latest run's id).

set role app_migrator;

alter table app.page_analysis add column retry_job_id uuid;

reset role;
