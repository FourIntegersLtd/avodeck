-- Undo 0066: destroying a case key no longer notifies; caches fall back to the 30 second check.
set role app_migrator;

create or replace function app.destroy_case_key(p_case_id uuid) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may destroy its key';
  end if;
  update app.case_keys set wrapped_key = null, destroyed_at = now()
  where case_id = p_case_id and destroyed_at is null;
  insert into app.audit_log (case_id, action, target) values (p_case_id, 'case.key_destroyed', 'case:' || p_case_id::text);
end
$$;

reset role;
