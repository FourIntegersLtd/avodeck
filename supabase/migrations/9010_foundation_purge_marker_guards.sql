-- 9010 foundation: every guard that stood aside for the purge setting now asks the marker (audit 16, PV-20).
--
-- 0075 made the purge exemption a row only `app.purge_case_rows` can write. Here the purge writes it (the body is
-- 9007's, plus the marker), and the guards that read `app.purging_case_id` read `app.case_is_purging(case_id)`
-- instead, by `create or replace` so no slice's migration is edited: `append_only_log` (0202; page reads, check log,
-- opinion versions, transcript views), `case_parties_guard` (0380), `report_versions_no_delete` (0017),
-- `report_rows_locked` (0610) and `fact_flags_keep_history` (0228). The lock-order triggers (0136, 0140) read the
-- setting only to take locks in one order; they remove and refuse nothing. Run-last range: no later migration can
-- put the setting back.

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
  -- PV-20: the exemption the guards honour is this marker, which no app role can write (0075), not the setting.
  insert into app.case_purge_marks (purged_case, xid) values (p_case_id, txid_current())
    on conflict (purged_case) do update set xid = excluded.xid;
  perform set_config('app.purging_case_id', p_case_id::text, true);  -- now read only by the lock-order triggers
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
  delete from app.case_purge_marks where purged_case = p_case_id;

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

create or replace function app.append_only_log() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and app.case_is_purging(old.case_id) then
    return old;
  end if;
  perform app.refuse(format('%s is append-only', tg_table_name));
  return null;
end
$$;

create or replace function app.case_parties_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and app.case_is_purging(old.case_id) then
    return old;
  end if;
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

create or replace function app.report_versions_no_delete() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.locked_at is not null and not app.case_is_purging(old.case_id) then
    perform app.report_locked('this report version is locked');
  end if;
  return old;
end
$$;

create or replace function app.report_rows_locked() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  -- The purge, and restores and support repairs run as the database owner (a login that is not the app's own),
  -- are not refused; anything reached from a session of the app's two roles is, even through a definer function.
  if app.case_is_purging(old.case_id) or session_user not in ('app_api', 'app_worker') then
    return case tg_op when 'DELETE' then old else new end;
  end if;
  if exists (select 1 from app.report_versions v
             where v.id = old.report_version_id and (v.locked_at is not null or v.status <> 'draft')) then
    perform app.report_locked('the rows of a locked report version cannot be changed');
  end if;
  return case tg_op when 'DELETE' then old else new end;
end
$$;

create or replace function app.fact_flags_keep_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if app.case_is_purging(old.case_id) then
    return old;
  end if;
  insert into app.fact_flag_history (fact_id, page_id, flagged_for, flagged_by, flagged_at, removed_by)
    select f.id, f.page_id, old.flagged_for, old.person_id, old.at, app.current_person_id()
    from app.facts f where f.id = old.fact_id;
  return old;
end
$$;

reset role;
