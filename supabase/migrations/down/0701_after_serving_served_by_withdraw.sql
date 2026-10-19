-- Down for 0701 after serving: back to the 0700 functions; the served_by lookup is not restored
-- (the handler reads `ReportServed.served_by`).
set role app_migrator;

drop function if exists app.after_serving_cancel_deadlines(uuid);
delete from app.job_kind_actions where kind in ('find_dependents', 'build_supplementary');

create or replace function app.after_serving_due_deadlines(p_run_date date, p_days integer)
  returns table (deadline_id uuid, case_id uuid, organisation_id uuid, named_expert_id uuid, kind text,
                 due_on date, last_reminded_on date)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select d.id, d.case_id, c.organisation_id, c.named_expert_id, d.kind, d.due_on, d.last_reminded_on
  from app.deadlines d join app.cases c on c.id = d.case_id
  where d.done_at is null and d.due_on between p_run_date and p_run_date + p_days
    and c.status not in ('settled', 'withdrawn', 'archived', 'deleted')
  order by d.due_on, d.id
$$;

create or replace function app.deadlines_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if new.case_id <> old.case_id or new.kind <> old.kind or new.created_from <> old.created_from
     or new.created_at <> old.created_at or new.timezone <> old.timezone then
    perform app.refuse('a deadline keeps its case, kind, origin and timezone');
  end if;
  if new.due_on <> old.due_on and not exists (
    select 1 from app.deadline_changes c
    where c.deadline_id = new.id and c.old_due_on = old.due_on and c.new_due_on = new.due_on and c.at = now()
  ) then
    perform app.refuse('moving a deadline needs a deadline change with its reason');
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;

alter table app.deadlines drop column if exists cancelled_at;

reset role;
