-- 0046 foundation: fix purge_case_rows for the bundle split (ADR 0034).
--
-- `purge_case_rows` (0017) deleted every table in `case_purge_order()` with a generic
-- `delete from app.%I where case_id = $1`. Several tables in that order no longer have a
-- case_id column: they moved to bundle_id when the bundle became a shared, first-class
-- entity (0040-0045, pipeline 0127-0130). A bundle can in principle be referenced by more
-- than one case, so purging one case must not delete another live case's page data — it
-- deletes the bundle-scoped rows only when no other non-deleted case still points at the
-- same bundle, and leaves the bundle row itself alone (it carries no patient text and is
-- harmless to leave orphaned; a future housekeeping pass may reap it).

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
  bundle uuid;
  bundle_shared boolean;
begin
  perform set_config('app.purging_case_id', p_case_id::text, true);
  select bundle_id into bundle from app.cases where id = p_case_id;
  bundle_shared := bundle is not null and exists (
    select 1 from app.cases c where c.bundle_id = bundle and c.id <> p_case_id and c.status <> 'deleted'
  );
  foreach t in array app.case_purge_order() loop
    continue when t in ('cases', 'case_keys', 'bundles') or to_regclass('app.' || t) is null;
    if t in ('ocr_words', 'page_analysis', 'page_embeddings', 'record_entries', 'document_parts',
             'document_scans', 'exhibits', 'pages', 'documents') then
      if bundle_shared then
        n := 0;  -- another live case still reads this bundle; its page data stays
      else
        execute format('delete from app.%I where bundle_id = $1', t) using bundle;
        get diagnostics n = row_count;
      end if;
    else
      execute format('delete from app.%I where case_id = $1', t) using p_case_id;
      get diagnostics n = row_count;
    end if;
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
