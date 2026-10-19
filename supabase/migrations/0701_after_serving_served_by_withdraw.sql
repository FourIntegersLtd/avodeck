-- 0701 after serving:
-- * The report-served handler now acts as `ReportServed.served_by` (set by slice F), so the ten-minute
--   lookup `app.after_serving_served_by` goes.
-- * The two jobs that change the opinion need `opinion.write` (only the named expert), not the
--   `case.upload` default: `find_dependents` and `build_supplementary` (foundation table, rows only).
-- * Withdrawing a case cancels its open deadlines: `deadlines.cancelled_at`, set once by
--   `app.after_serving_cancel_deadlines` when the case is withdrawn, never cleared; the reminder run
--   skips cancelled deadlines.

set role app_migrator;

drop function if exists app.after_serving_served_by(uuid, uuid);

insert into app.job_kind_actions (kind, action) values
  ('find_dependents', 'opinion.write'),
  ('build_supplementary', 'opinion.write')
on conflict (kind) do update set action = excluded.action;

alter table app.deadlines add column cancelled_at timestamptz;

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
  if old.cancelled_at is not null and new.cancelled_at is distinct from old.cancelled_at then
    perform app.refuse('a cancelled deadline stays cancelled');
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

-- Cancel every open deadline of a withdrawn case; returns how many. Only someone who may withdraw the
-- case (the named expert), and only once the case is withdrawn.
create function app.after_serving_cancel_deadlines(p_case uuid) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_count integer;
begin
  if not coalesce(app.can_access_case(p_case, 'case.withdraw'), false) then
    raise exception using errcode = '42501', message = 'cancelling deadlines needs case.withdraw';
  end if;
  if not exists (select 1 from app.cases c where c.id = p_case and c.status = 'withdrawn') then
    perform app.refuse('only a withdrawn case has its deadlines cancelled');
  end if;
  update app.deadlines set cancelled_at = now()
  where case_id = p_case and done_at is null and cancelled_at is null;
  get diagnostics v_count = row_count;
  return v_count;
end
$$;
revoke all on function app.after_serving_cancel_deadlines(uuid) from public;
grant execute on function app.after_serving_cancel_deadlines(uuid) to app_api;

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
  where d.done_at is null and d.cancelled_at is null and d.due_on between p_run_date and p_run_date + p_days
    and c.status not in ('settled', 'withdrawn', 'archived', 'deleted')
  order by d.due_on, d.id
$$;

reset role;
