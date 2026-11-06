-- 0139 pipeline: a page batch's link to its child job may be cleared (ADR 0038; review of 0136).
--
-- `pipeline_batches.child_job_id` is set once (trigger `pipeline_batches_child_once`), but its foreign key is
-- `on delete set null`: deleting a child job (the case purge) fires the cascade's own UPDATE to null, and the set-once
-- trigger refused it. The rule is "never changed to a different job"; clearing to null is the cascade and is allowed.

set role app_migrator;

create or replace function app.pipeline_batch_child_once() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.child_job_id is not null and new.child_job_id is not null
     and new.child_job_id is distinct from old.child_job_id then
    raise exception 'a page batch keeps the child job it was given' using errcode = 'P0001';
  end if;
  return new;
end
$$;

reset role;
