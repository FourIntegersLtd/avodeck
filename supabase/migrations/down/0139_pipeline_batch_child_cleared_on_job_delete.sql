-- 0139 down: the set-once trigger as 0136 had it (clearing to null refused).

set role app_migrator;

create or replace function app.pipeline_batch_child_once() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.child_job_id is not null and new.child_job_id is distinct from old.child_job_id then
    raise exception 'a page batch keeps the child job it was given' using errcode = 'P0001';
  end if;
  return new;
end
$$;

reset role;
