-- 0132 down: the retry run marker.

set role app_migrator;

alter table app.page_analysis drop column if exists retry_job_id;

reset role;
