-- Slice H (close and compliance): legal holds, deletion certificates, and the close functions.
--
-- legal_holds: pauses deletion of a case (architecture 10.12, F30). Set and released by the named
-- expert or an organisation owner (legal_hold.set / legal_hold.release, step-up in the API). The
-- reason is a short code, never free text. Only the release changes after a hold is set. Purged
-- with the case (it is in app.case_purge_order()). No hold can be set once deletion has started.
--
-- deletion_certificates: proof of deletion (rule 6: counts, hashes and dates only). Written only by
-- the case's own deletion job; read by whoever may case.view_certificate. Kept after the case's
-- rows are purged (KEPT_AFTER_CASE_DELETION), so its foreign key is to the case tombstone.

set role app_migrator;

-- Legal holds --------------------------------------------------------------------------------------
create table app.legal_holds (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  reason text not null constraint legal_holds_reason_code check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  set_by uuid not null references app.people (id),
  until date,
  released_at timestamptz,
  released_by uuid references app.people (id),
  created_at timestamptz not null default now(),
  constraint legal_holds_released_together check ((released_at is null) = (released_by is null))
);
create index legal_holds_case on app.legal_holds (case_id) where released_at is null;

alter table app.legal_holds enable row level security;

create policy legal_holds_view on app.legal_holds for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy legal_holds_set on app.legal_holds for insert to app_api
  with check (app.can_access_case(case_id, 'legal_hold.set') and set_by = app.current_person_id());
create policy legal_holds_release on app.legal_holds for update to app_api
  using (app.can_access_case(case_id, 'legal_hold.release'))
  with check (app.can_access_case(case_id, 'legal_hold.release'));
create policy legal_holds_job on app.legal_holds for select to app_worker
  using (case_id = app.current_job_case_id());

revoke all on app.legal_holds from app_api, app_worker;
grant select, insert on app.legal_holds to app_api;
grant update (released_at, released_by) on app.legal_holds to app_api;
grant select on app.legal_holds to app_worker;

-- No new hold once the case's deletion has started (its certificate row exists): the run is
-- already part done. The case row is locked so the deletion job's start and a new hold queue.
create function app.legal_holds_not_during_deletion() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  perform 1 from app.cases c where c.id = new.case_id for update;
  if exists (select 1 from app.deletion_certificates d where d.case_id = new.case_id) then
    perform app.refuse('this case is already being deleted');
  end if;
  return new;
end
$$;

-- Deletion certificates ------------------------------------------------------------------------------
create table app.deletion_certificates (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null unique references app.cases (id),
  deleted_at timestamptz,                         -- null while the deletion is still running
  counts jsonb not null default '{}',             -- rows removed per table, objects removed from Storage
  hashes jsonb not null default '{}',             -- SHA-256 of the files that were held
  log_chain_heads jsonb not null default '{}',    -- last hash of each hash-chained log that was removed
  backup_expiry_on date,                          -- when the last backup holding the case expires
  sent_to text[] not null default '{}',           -- who was sent it, as role codes (never addresses)
  created_at timestamptz not null default now(),
  constraint deletion_certificates_sent_to_codes check (
    array_to_string(sent_to, ',') ~ '^([a-z][a-z0-9_]{0,39}(,[a-z][a-z0-9_]{0,39})*)?$')
);

alter table app.deletion_certificates enable row level security;

create policy deletion_certificates_view on app.deletion_certificates for select to app_api
  using (app.can_access_case(case_id, 'case.view_certificate'));
create policy deletion_certificates_job on app.deletion_certificates to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

revoke all on app.deletion_certificates from app_api, app_worker;
grant select on app.deletion_certificates to app_api;
grant select, insert, update on app.deletion_certificates to app_worker;

create trigger legal_holds_not_during_deletion before insert on app.legal_holds
  for each row execute function app.legal_holds_not_during_deletion();

-- The case's audit trail (GET /cases/{id}/audit). audit_log shows a person only their own rows,
-- so this reads them for whoever may case.view_audit. Ids and short codes only.
create function app.close_case_audit(p_case_id uuid)
  returns table (seq bigint, at timestamptz, action text, target text, person_id uuid, organisation_id uuid)
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if not app.can_access_case(p_case_id, 'case.view_audit') then
    raise exception using errcode = '42501', message = 'this needs case.view_audit';
  end if;
  return query
    select a.seq, a.at, a.action, a.target, a.person_id, a.organisation_id
    from app.audit_log a where a.case_id = p_case_id order by a.seq;
end
$$;

-- Who the certificate goes to, for the case's own deletion job: role code and address.
create function app.close_certificate_recipients(p_case_id uuid)
  returns table (role text, email text)
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may read this';
  end if;
  return query
    select 'named_expert'::text, p.email
    from app.cases c join app.people p on p.id = c.named_expert_id
    where c.id = p_case_id;
end
$$;

-- Daily: settled cases move to the archive `p_days` after settlement. Returns how many moved.
create function app.close_auto_archive(p_run_date date, p_days integer) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  moved integer;
begin
  with archived as (
    update app.cases c set status = 'archived'
    where c.status = 'settled' and c.settled_at::date <= p_run_date - p_days
    returning c.id
  )
  insert into app.audit_log (case_id, action, target)
  select a.id, 'case.archived', 'case:' || a.id::text from archived a;
  get diagnostics moved = row_count;
  return moved;
end
$$;

-- Daily: holds whose end date has passed are released (released_by = the person who set them).
create function app.close_release_expired_holds(p_run_date date) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  released integer;
begin
  with ended as (
    update app.legal_holds h set released_at = now(), released_by = h.set_by
    where h.released_at is null and h.until is not null and h.until < p_run_date
    returning h.id, h.case_id
  )
  insert into app.audit_log (case_id, action, target)
  select e.case_id, 'legal_hold.expired', 'legal_hold:' || e.id::text from ended e;
  get diagnostics released = row_count;
  return released;
end
$$;

revoke execute on function app.legal_holds_not_during_deletion(), app.close_case_audit(uuid),
  app.close_certificate_recipients(uuid), app.close_auto_archive(date, integer),
  app.close_release_expired_holds(date)
  from public, app_api, app_worker;
grant execute on function app.close_case_audit(uuid) to app_api;
grant execute on function app.close_certificate_recipients(uuid), app.close_auto_archive(date, integer),
  app.close_release_expired_holds(date) to app_worker;

reset role;
