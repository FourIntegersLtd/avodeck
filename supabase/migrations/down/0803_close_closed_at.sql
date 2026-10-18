set role app_migrator;

create or replace function app.close_withdrawn_day(p_case_id uuid) returns date
  language sql stable
  security definer
  set search_path = ''
as $$
  select max(a.at)::date from app.audit_log a where a.case_id = p_case_id and a.action = 'case.withdrawn'
$$;

create function app.close_case_withdrawn() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.audit_log (case_id, person_id, action, target)
  values (new.id, app.current_person_id(), 'case.withdrawn', 'case:' || new.id::text);
  return null;
end
$$;
create trigger close_case_withdrawn after update of status on app.cases
  for each row when (new.status = 'withdrawn' and old.status is distinct from 'withdrawn')
  execute function app.close_case_withdrawn();
revoke execute on function app.close_case_withdrawn() from public, app_api, app_worker;

reset role;
