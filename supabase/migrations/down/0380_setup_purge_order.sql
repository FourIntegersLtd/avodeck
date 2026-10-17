-- Down for 0380 setup: the 0017 purge order and the 0310 parties guard.
set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'jobs', 'facts', 'uploads', 'pages', 'documents', 'report_versions', 'questions',
               'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_members', 'legal_holds',
               'case_keys', 'cases']
$$;

create or replace function app.case_parties_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if app.case_ever_served(coalesce(new.case_id, old.case_id)) then
    perform app.refuse('the parties are fixed once the case is served');
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  new.version := old.version + 1;
  new.updated_at := now();
  return new;
end
$$;
  for each row execute function app.case_parties_guard();

create trigger case_instructing_parties_version before insert or update on app.case_instructing_parties
  for each row execute function app.setup_bump_version();
create trigger case_instructions_version before insert or update on app.case_instructions
  for each row execute function app.setup_bump_version();

create or replace function app.case_subject_keys_touch() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end
$$;

reset role;
