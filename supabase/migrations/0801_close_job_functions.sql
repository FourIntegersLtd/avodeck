-- Slice H: functions for the deletion job and the daily close run.
--
-- close_log_chain_heads: the last hash of each hash-chained case log (the read and verification
-- logs, once their slices exist), for the deletion certificate (architecture 10.9). Hashes only.
--
-- close_cases_without_retention: closed cases (settled, archived, withdrawn) that have no deletion
-- date yet, with their pack codes, so the daily run can give each one its date from the packs.

set role app_migrator;

create function app.close_log_chain_heads(p_case_id uuid) returns jsonb
  language plpgsql stable
  security definer
  set search_path = ''
as $$
declare
  t text;
  head text;
  heads jsonb := '{}';
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may read this';
  end if;
  foreach t in array array['page_reads', 'fact_verifications'] loop
    continue when to_regclass('app.' || t) is null;
    execute format(
      'select r.hash::text from app.%1$I r where r.case_id = $1 and r.hash is not null'
      ' and not exists (select 1 from app.%1$I n where n.case_id = $1 and n.prev_hash = r.hash)'
      ' order by r.hash::text limit 1', t)
      into head using p_case_id;
    if head is not null then
      heads := heads || jsonb_build_object(t, head);
    end if;
  end loop;
  return heads;
end
$$;

create function app.close_cases_without_retention(p_run_date date)
  returns table (case_id uuid, jurisdiction text, case_type text, specialty text, retention_policy text)
  language sql stable
  security definer
  set search_path = ''
as $$
  select c.id, c.jurisdiction, c.case_type, c.specialty, c.retention_policy
  from app.cases c
  where c.status in ('settled', 'archived', 'withdrawn') and c.delete_after is null
  order by c.id
$$;

revoke execute on function app.close_log_chain_heads(uuid), app.close_cases_without_retention(date)
  from public, app_api, app_worker;
grant execute on function app.close_log_chain_heads(uuid), app.close_cases_without_retention(date) to app_worker;

reset role;
