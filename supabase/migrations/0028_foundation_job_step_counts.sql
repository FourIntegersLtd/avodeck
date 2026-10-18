-- 0028 foundation: jobs count their steps ("page 40 of 900", screen 07).
-- `step_done` of `step_total` are whole numbers, null until a job reports them; both or neither. They
-- belong to the current `step`; `ctx.progress()` starts them again when the step changes.
-- Rule 6: numbers only, never text.

set role app_migrator;

alter table app.jobs
  add column step_done integer,
  add column step_total integer,
  add constraint jobs_step_counts check (
    (step_done is null and step_total is null)
    or (step_done is not null and step_total is not null and step_total > 0 and step_done between 0 and step_total)
  );

reset role;
