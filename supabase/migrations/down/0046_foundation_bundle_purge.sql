-- Undo 0046: restore purge_case_rows to its 0017 body (generic delete by case_id, no bundle
-- special-casing).
set role app_migrator;

create or replace function app.purge_case_rows(p_case_id uuid) returns jsonb
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  t text;
  n bigint;
  counts jsonb := '{}';
begin
  perform set_config('app.purging_case_id', p_case_id::text, true);
  foreach t in array app.case_purge_order() loop
    continue when t in ('cases', 'case_keys') or to_regclass('app.' || t) is null;
    execute format('delete from app.%I where case_id = $1', t) using p_case_id;
    get diagnostics n = row_count;
    counts := counts || jsonb_build_object(t, n);
  end loop;
  perform set_config('app.purging_case_id', '', true);

  update app.cases set status = 'deleted', label = null, subject_status = null, party_role = null,
    instructing_party_type = null, court_track = null, due_date = null, period_start = null,
    period_end = null, stage = null
  where id = p_case_id
    and (status, label, subject_status, party_role, instructing_party_type, court_track, due_date,
         period_start, period_end, stage)
        is distinct from ('deleted', null, null, null, null, null, null, null, null, null);
  get diagnostics n = row_count;
  if n > 0 then
    insert into app.audit_log (case_id, action, target) values (p_case_id, 'case.purged', 'case:' || p_case_id::text);
  end if;
  insert into app.deletion_ledger (case_id) values (p_case_id) on conflict (case_id) do nothing;
  return counts;
end
$$;

reset role;
