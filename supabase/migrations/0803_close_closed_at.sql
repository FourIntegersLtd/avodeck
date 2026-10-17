-- 0803 close: the withdrawal day comes from `cases.closed_at` (foundation 0022), set by the
-- database when a case is withdrawn. The audit-log trigger of 0802 is no longer needed: the audit
-- row of a withdrawal is the one the route records.

set role app_migrator;

drop trigger close_case_withdrawn on app.cases;
drop function app.close_case_withdrawn();

create or replace function app.close_withdrawn_day(p_case_id uuid) returns date
  language sql stable
  security definer
  set search_path = ''
as $$
  select c.closed_at::date from app.cases c where c.id = p_case_id and c.settled_at is null
$$;

reset role;
