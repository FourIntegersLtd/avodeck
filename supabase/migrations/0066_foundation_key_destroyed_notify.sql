-- 0066 foundation: destroying a case key tells every process to forget it (scalability PR 15).
--
-- A case key is cached unwrapped for up to 5 minutes in each API and worker process, and the reader keeps the
-- case's decrypted words for up to 10. `app.destroy_case_key` now also sends `pg_notify('case_key_destroyed',
-- '<case id>')`, delivered when the job's transaction commits. The payload is the case id and nothing else
-- (rule 6). Each process's listener clears that case from both caches. Who may destroy a key and what is
-- audited are unchanged: only the case's own job, and one audit row.

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
  perform pg_catalog.pg_notify('case_key_destroyed', p_case_id::text);
end
$$;

reset role;
