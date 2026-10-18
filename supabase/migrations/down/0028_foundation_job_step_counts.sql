set role app_migrator;

alter table app.jobs
  drop constraint jobs_step_counts,
  drop column step_total,
  drop column step_done;

reset role;
