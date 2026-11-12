-- 9007 foundation: purging a case also removes its finished queue rows (audit 15 BG-14; closes audit 12 D1f).
--
-- `procrastinate.procrastinate_jobs` and its events hold only ids and times, but a deleted case should leave none.
-- The body is 0046's `purge_case_rows` with one added `delete`. A queue row still `doing` (the delete job's own) is
-- left and goes with the other tombstones in the daily `app.prune_queue_rows` (0071). Last foundation range, so a
-- later redefinition cannot undo it. Same signature, security definer, search path and grants.

set role app_migrator;

-- Its own function: the queue's triggers name their tables bare, so the delete runs on the queue schema's search path
-- (`purge_case_rows` has an empty one).
create function app.purge_case_queue_rows(p_case_id uuid) returns integer
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  n integer;
begin
  delete from procrastinate_jobs
   where args ->> 'case_id' = p_case_id::text and status in ('succeeded', 'failed', 'cancelled', 'aborted');
  get diagnostics n = row_count;
  return n;
end
$$;
revoke execute on function app.purge_case_queue_rows(uuid) from public, app_api, app_worker;

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

  -- BG-14, audit 12 D1f: the case's finished queue rows (ids only, but a deleted case leaves nothing). A row still
  -- being worked, the delete's own, is removed with the other tombstones by `app.prune_queue_rows`.
  perform app.purge_case_queue_rows(p_case_id);

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
