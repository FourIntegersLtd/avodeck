-- 0017 foundation: what slice H (close and compliance) builds on (architecture 4.10, 10.9; F29).
--
-- Cases
--   settled_at          set by the database when a case is settled, kept when it is archived,
--                       cleared when it is reopened (the retention clock restarts from the next
--                       settlement, state-machines.md). Nobody writes it by hand.
--   retention_policy    a short code (the policies themselves come from the packs and slice H).
--   label               emptied only on a deleted case's tombstone.
--   status moves        → settled `case.settle`, → archived `case.archive`, settled or archived →
--                       active `case.reopen` (named expert only; no step-up).
-- Actions               case.settle, case.archive, case.reopen, case.view_audit, case.view_certificate.
-- Deleting a case
--   app.case_purge_order()          the order rows go, children first (= PURGE_ORDER in
--                                   api/app/core/contracts/case_tables.py).
--   app.purge_case(case_id)         the worker, inside the case's own job: removes every row of the
--                                   case from every case table except `cases` (left as a tombstone)
--                                   and `case_keys` (destroyed by app.destroy_case_key()), writes
--                                   `deletion_ledger` and returns the rows removed per table.
--                                   Refused while the case is on hold or under an active legal hold.
--   report_versions                 a locked version still refuses deletion (AVLCK) except here.
--   audit_log                       kept unchanged: it holds ids and short codes only, and its hash
--                                   chain covers every column, so rewriting it would break the chain.
--   deletion_ledger                 append-only list of deleted cases (worker insert only).
--   app.replay_deletion_ledger()    after a backup restore, before the service reopens (operators).
--   app.cases_due_for_deletion(d)   for the daily sweep (worker): cases whose delete_after has come.

set role app_migrator;

-- Cases ----------------------------------------------------------------------------------------
alter table app.cases add column settled_at timestamptz;
alter table app.cases
  add constraint cases_retention_policy_code check (retention_policy ~ '^[a-z][a-z0-9_]{0,39}$');
alter table app.cases alter column label drop not null;
alter table app.cases add constraint cases_label_until_deleted check (label is not null or status = 'deleted');

create function app.cases_settled_at() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.settled_at := null;
  elsif new.status is distinct from old.status and new.status = 'settled' then
    new.settled_at := now();
  elsif new.status is distinct from old.status and new.status = 'active' then
    new.settled_at := null;
  else
    new.settled_at := old.settled_at;
  end if;
  return new;
end
$$;

-- Named to run after cases_guard (triggers fire in name order).
create trigger cases_settled_at before insert or update on app.cases
  for each row execute function app.cases_settled_at();

-- The permissions matrix, with the close actions ---------------------------------------------------
create or replace function app.case_action_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(bool_or(
           case_role = any (r.case_roles)
           or (org_role in ('owner', 'admin') and case r.org_grant
                 when 'admin' then true
                 when 'owner' then org_role = 'owner'
                 when 'team_admin' then org_kind = 'team'
                 else false end)
         ), false)
  from (values
    ('case.view_metadata',        '{named_expert,co_expert,supervisor,assistant,viewer}'::text[], 'admin'),
    ('case.view_content',         '{named_expert,co_expert,supervisor,assistant,viewer}', 'none'),
    ('case.edit_setup',           '{named_expert,assistant}', 'none'),
    ('case.upload',               '{named_expert,assistant}', 'none'),
    ('case.manage_members',       '{named_expert}', 'admin'),
    ('case.reassign_named_expert','{}', 'admin'),
    ('page.read',                 '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('page.quarantine',           '{named_expert,assistant}', 'none'),
    ('page.replace',              '{named_expert,assistant}', 'none'),
    ('page.rotate',               '{named_expert,assistant}', 'none'),
    ('fact.verify',               '{named_expert}', 'none'),
    ('question.confirm',          '{named_expert}', 'none'),
    ('opinion.write',             '{named_expert}', 'none'),
    ('comment.write',             '{named_expert,co_expert,supervisor,assistant}', 'none'),
    ('report.build',              '{named_expert,assistant}', 'none'),
    ('report.sign',               '{named_expert}', 'none'),
    ('report.serve',              '{named_expert}', 'none'),
    ('report.withdraw',           '{named_expert}', 'none'),
    ('request.draft',             '{named_expert,assistant}', 'none'),
    ('request.send',              '{named_expert}', 'none'),
    ('invoice.create',            '{named_expert,assistant}', 'team_admin'),
    ('invoice.send',              '{named_expert}', 'team_admin'),
    ('credit_note.issue',         '{named_expert}', 'team_admin'),
    ('invoice.record_payment',    '{named_expert,assistant}', 'admin'),
    ('case.export',               '{named_expert}', 'admin'),
    ('case.retention',            '{named_expert}', 'admin'),
    ('legal_hold.set',            '{named_expert}', 'owner'),
    ('legal_hold.release',        '{named_expert}', 'owner'),
    ('case.withdraw',             '{named_expert}', 'none'),
    ('support_access.grant',      '{named_expert}', 'none'),
    ('case.delete_now',           '{named_expert}', 'owner'),
    ('case.settle',               '{named_expert}', 'none'),
    ('case.archive',              '{named_expert}', 'none'),
    ('case.reopen',               '{named_expert}', 'none'),
    ('case.view_audit',           '{named_expert}', 'admin'),
    ('case.view_certificate',     '{named_expert}', 'admin')
  ) as r (action, case_roles, org_grant)
  where r.action = case_action_allowed.action
$$;

create or replace function app.cases_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  own constant text[] := array['named_expert_id', 'retention_policy', 'delete_after', 'on_hold', 'status',
                               'version', 'updated_at', 'settled_at'];
  needed text[] := '{}';
  action text;
begin
  if current_user <> 'app_api' then
    return new;
  end if;
  if new.named_expert_id is distinct from old.named_expert_id then
    if app.case_ever_served(old.id) then
      raise exception using errcode = '42501', message = 'the named expert cannot change after serving';
    end if;
    needed := array_append(needed, 'case.reassign_named_expert');
  end if;
  if (new.retention_policy, new.delete_after, new.on_hold)
     is distinct from (old.retention_policy, old.delete_after, old.on_hold) then
    needed := array_append(needed, 'case.retention');
  end if;
  if new.status is distinct from old.status then
    if new.status = 'deleted' and app.case_ever_served(old.id) then
      raise exception using errcode = '42501', message = 'a served case is deleted only by its retention';
    end if;
    needed := array_append(needed, case
      when new.status = 'deleted' then 'case.delete_now'
      when new.status = 'withdrawn' then 'case.withdraw'
      when new.status = 'served' then 'report.serve'
      when new.status = 'settled' then 'case.settle'
      when new.status = 'archived' then 'case.archive'
      when new.status = 'active' and old.status in ('settled', 'archived') then 'case.reopen'
      else 'case.edit_setup' end);
  end if;
  if (to_jsonb(new) - own) is distinct from (to_jsonb(old) - own) then
    needed := array_append(needed, 'case.edit_setup');
  end if;
  foreach action in array needed loop
    if not app.can_access_case(old.id, action) then
      raise exception using errcode = '42501', message = 'this change needs ' || action;
    end if;
  end loop;
  return new;
end
$$;

-- The deletion ledger (F29) ---------------------------------------------------------------------
-- No foreign key: the ledger must outlive whatever a restore brings back or leaves out.
create table app.deletion_ledger (
  case_id uuid primary key,
  deleted_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create function app.deletion_ledger_append_only() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  perform app.refuse('the deletion ledger is append-only');
  return null;
end
$$;

-- For each statement, so any attempt is refused, even one that matches no row.
create trigger deletion_ledger_append_only before update or delete or truncate on app.deletion_ledger
  for each statement execute function app.deletion_ledger_append_only();

alter table app.deletion_ledger enable row level security;
revoke all on app.deletion_ledger from app_api, app_worker;
grant select, insert on app.deletion_ledger to app_worker;
create policy deletion_ledger_job on app.deletion_ledger for select to app_worker
  using (case_id = app.current_job_case_id());
create policy deletion_ledger_job_write on app.deletion_ledger for insert to app_worker
  with check (case_id = app.current_job_case_id());

-- A locked report version is removed only by purge_case (rule 8) ------------------------------------
create function app.report_versions_no_delete() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.locked_at is not null and not (
       current_user = 'app_migrator'
       and current_setting('app.purging_case_id', true) is not distinct from old.case_id::text) then
    perform app.report_locked('this report version is locked');
  end if;
  return old;
end
$$;

create trigger report_versions_no_delete before delete on app.report_versions
  for each row execute function app.report_versions_no_delete();

-- Deleting a case -------------------------------------------------------------------------------
create function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'jobs', 'facts', 'uploads', 'pages', 'documents', 'report_versions', 'questions',
               'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_members', 'legal_holds',
               'case_keys', 'cases']
$$;

-- An active legal hold: not released and not past its end date. `legal_holds` is slice H's table;
-- until it exists nothing is held by it.
create function app.case_legally_held(p_case_id uuid) returns boolean
  language plpgsql stable
  security definer
  set search_path = ''
as $$
declare
  held boolean := false;
begin
  if to_regclass('app.legal_holds') is not null then
    execute 'select exists (select 1 from app.legal_holds h where h.case_id = $1 and h.released_at is null'
            ' and (h.until is null or h.until >= current_date))'
      into held using p_case_id;
  end if;
  return held;
end
$$;

-- Removes the rows; no checks. Called by purge_case and replay_deletion_ledger only.
create function app.purge_case_rows(p_case_id uuid) returns jsonb
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

create function app.purge_case(p_case_id uuid) returns jsonb
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may delete it';
  end if;
  if exists (select 1 from app.cases c where c.id = p_case_id and c.on_hold) or app.case_legally_held(p_case_id) then
    perform app.refuse('this case is on hold');
  end if;
  return app.purge_case_rows(p_case_id);
end
$$;

-- After a backup restore, before the service reopens: every case in the ledger is removed again
-- and its key destroyed again. Returns how many cases a restore had brought back.
create function app.replay_deletion_ledger() returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  l record;
  counts jsonb;
  restored integer := 0;
  keys bigint;
  tomb boolean;
begin
  for l in select d.case_id from app.deletion_ledger d order by d.deleted_at loop
    select c.status <> 'deleted' into tomb from app.cases c where c.id = l.case_id;
    counts := app.purge_case_rows(l.case_id);
    update app.case_keys set wrapped_key = null, destroyed_at = now()
    where case_id = l.case_id and destroyed_at is null;
    get diagnostics keys = row_count;
    if keys > 0 then
      insert into app.audit_log (case_id, action, target)
      values (l.case_id, 'case.key_destroyed', 'case:' || l.case_id::text);
    end if;
    if coalesce(tomb, false) or keys > 0
       or exists (select 1 from jsonb_each_text(counts) e where e.value::bigint > 0) then
      restored := restored + 1;
    end if;
  end loop;
  return restored;
end
$$;

-- For the daily deletion sweep (an anonymous worker connection): cases whose delete_after has come.
create function app.cases_due_for_deletion(p_run_date date)
  returns table (case_id uuid, held boolean)
  language sql stable
  security definer
  set search_path = ''
as $$
  select c.id, c.on_hold or app.case_legally_held(c.id)
  from app.cases c
  where c.delete_after <= p_run_date and c.status <> 'deleted'
  order by c.delete_after, c.id
$$;

revoke execute on function app.cases_settled_at(), app.deletion_ledger_append_only(),
  app.report_versions_no_delete(), app.case_purge_order(), app.case_legally_held(uuid),
  app.purge_case_rows(uuid), app.purge_case(uuid), app.replay_deletion_ledger(),
  app.cases_due_for_deletion(date)
  from public, app_api, app_worker;
grant execute on function app.case_purge_order(), app.purge_case(uuid), app.cases_due_for_deletion(date)
  to app_worker;

reset role;
