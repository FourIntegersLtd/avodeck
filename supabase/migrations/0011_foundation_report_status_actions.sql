-- 0011 foundation: only the named expert signs, serves and withdraws a report version (rule 9).
-- `report_versions_change` lets anyone with `report.build` (named expert or assistant) update a row,
-- which is right for drafts. Moving `status` is a different action, so the database repeats the
-- API's check for the person the restricted role acts for: draft → locked needs `report.sign`,
-- locked → served `report.serve`, served → withdrawn `report.withdraw`. The worker (app_worker)
-- acts for its own case's job and is not checked here; `report_versions_guard` still allows only
-- the documented moves.

set role app_migrator;

create function app.report_versions_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  needed text;
begin
  if current_user <> 'app_api' or new.status is not distinct from old.status then
    return new;
  end if;
  needed := case new.status
    when 'locked' then 'report.sign'
    when 'served' then 'report.serve'
    when 'withdrawn' then 'report.withdraw'
    else 'report.build' end;
  if not app.can_access_case(old.case_id, needed) then
    raise exception using errcode = '42501', message = 'this change needs ' || needed;
  end if;
  return new;
end
$$;

-- Named to run before report_versions_guard (triggers fire in name order).
create trigger report_versions_actor before update on app.report_versions
  for each row execute function app.report_versions_actor();

reset role;
