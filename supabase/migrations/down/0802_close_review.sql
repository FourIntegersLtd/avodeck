set role app_migrator;

drop function app.close_stale_exports(date, integer, integer);
drop function app.close_deletion_started(uuid);
drop trigger deletion_certificates_frozen on app.deletion_certificates;
drop function app.deletion_certificates_frozen();
drop trigger legal_holds_release_guard on app.legal_holds;
drop function app.legal_holds_release_guard();

create or replace function app.close_auto_archive(p_run_date date, p_days integer) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  moved integer;
begin
  with archived as (
    update app.cases c set status = 'archived'
    where c.status = 'settled' and c.settled_at::date <= p_run_date - p_days
    returning c.id
  )
  insert into app.audit_log (case_id, action, target)
  select a.id, 'case.archived', 'case:' || a.id::text from archived a;
  get diagnostics moved = row_count;
  return moved;
end
$$;

drop function app.close_withdrawn_on(uuid);
drop function app.close_withdrawn_day(uuid);
drop trigger close_case_withdrawn on app.cases;
drop function app.close_case_withdrawn();
-- The 'case.withdrawn' audit rows stay: the audit log is append-only.

reset role;
