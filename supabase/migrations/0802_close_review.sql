-- Slice H: fixes after review (architecture 10.12; F20, F30).
--
-- Withdrawal day     A withdrawn case has no settlement date, so its retention counts from the day
--                    it was withdrawn. `close_case_withdrawn` records that day in the audit log
--                    ('case.withdrawn', ids only) whenever a case moves to withdrawn;
--                    `app.close_withdrawn_on(case)` reads it back for the case's members and its
--                    own job. Cases withdrawn before this migration get today.
-- Auto-archive       `app.close_auto_archive` also archives withdrawn cases `p_days` after withdrawal.
-- Releasing a hold   the running app (app_api) may only release an unreleased hold, now, in the name
--                    of the person releasing it; a release is never undone.
-- Certificates       a finished certificate (deleted_at set) never changes.
-- Deletion started   `app.close_deletion_started(case)` tells every case member, whatever their
--                    role, that the deletion run has started (the certificate itself stays theirs
--                    who may case.view_certificate).
-- Old exports        `app.close_stale_exports(run_date, days, window)` lists finished export jobs
--                    whose files the daily run removes when nobody downloaded them in time.

set role app_migrator;

-- Withdrawal day ----------------------------------------------------------------------------------
create function app.close_case_withdrawn() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.audit_log (case_id, person_id, action, target)
  values (new.id, app.current_person_id(), 'case.withdrawn', 'case:' || new.id::text);
  return null;
end
$$;

create trigger close_case_withdrawn after update of status on app.cases
  for each row when (new.status = 'withdrawn' and old.status is distinct from 'withdrawn')
  execute function app.close_case_withdrawn();

insert into app.audit_log (case_id, action, target)
select c.id, 'case.withdrawn', 'case:' || c.id::text
from app.cases c
where c.status = 'withdrawn'
  and not exists (select 1 from app.audit_log a where a.case_id = c.id and a.action = 'case.withdrawn')
order by c.id;

create function app.close_withdrawn_day(p_case_id uuid) returns date
  language sql stable
  security definer
  set search_path = ''
as $$
  select max(a.at)::date from app.audit_log a where a.case_id = p_case_id and a.action = 'case.withdrawn'
$$;

create function app.close_withdrawn_on(p_case_id uuid) returns date
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id
     and not app.can_access_case(p_case_id, 'case.view_metadata') then
    raise exception using errcode = '42501', message = 'this needs case.view_metadata';
  end if;
  return app.close_withdrawn_day(p_case_id);
end
$$;

-- Auto-archive: settled or withdrawn cases `p_days` after they closed ------------------------------
create or replace function app.close_auto_archive(p_run_date date, p_days integer) returns integer
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  moved integer;
begin
  with archived as (
    update app.cases c set status = 'archived'
    where (c.status = 'settled' and c.settled_at::date <= p_run_date - p_days)
       or (c.status = 'withdrawn' and app.close_withdrawn_day(c.id) <= p_run_date - p_days)
    returning c.id
  )
  insert into app.audit_log (case_id, action, target)
  select a.id, 'case.archived', 'case:' || a.id::text from archived a;
  get diagnostics moved = row_count;
  return moved;
end
$$;

-- Releasing a hold ---------------------------------------------------------------------------------
-- Not security definer: current_user is whoever runs the update. The daily run (released_by = the
-- person who set it) runs as the definer of close_release_expired_holds and is not affected.
create function app.legal_holds_release_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if current_user = 'app_api' then
    if old.released_at is not null then
      perform app.refuse('this hold has already been released');
    end if;
    if new.released_at is null or new.released_at <> now()
       or new.released_by is distinct from app.current_person_id() then
      perform app.refuse('a hold is released now, by the person releasing it');
    end if;
  end if;
  return new;
end
$$;

create trigger legal_holds_release_guard before update on app.legal_holds
  for each row execute function app.legal_holds_release_guard();

-- A finished certificate never changes -------------------------------------------------------------
create function app.deletion_certificates_frozen() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.deleted_at is not null then
    perform app.refuse('a finished deletion certificate never changes');
  end if;
  return new;
end
$$;

create trigger deletion_certificates_frozen before update on app.deletion_certificates
  for each row execute function app.deletion_certificates_frozen();

-- Deletion started, for every case member -----------------------------------------------------------
create function app.close_deletion_started(p_case_id uuid) returns boolean
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id
     and not app.can_access_case(p_case_id, 'case.view_metadata') then
    raise exception using errcode = '42501', message = 'this needs case.view_metadata';
  end if;
  return exists (select 1 from app.deletion_certificates d where d.case_id = p_case_id)
      or exists (select 1 from app.cases c where c.id = p_case_id and c.status = 'deleted');
end
$$;

-- Old exports ------------------------------------------------------------------------------------------
-- Finished between `p_days + p_window` and `p_days` days before the run date (each day's run looks
-- back over the window, so a missed or failed day is caught up; removing a removed file is harmless).
create or replace function app.close_stale_exports(p_run_date date, p_days integer, p_window integer)
  returns table (case_id uuid, job_id uuid)
  language sql stable
  security definer
  set search_path = ''
as $$
  select j.case_id, j.id from app.jobs j
  where j.kind in ('export_case', 'export_transcript') and j.finished_at is not null
    and j.finished_at::date <= p_run_date - p_days
    and j.finished_at::date > p_run_date - p_days - p_window
  order by j.finished_at, j.id
$$;

revoke execute on function app.close_case_withdrawn(), app.close_withdrawn_day(uuid), app.close_withdrawn_on(uuid),
  app.close_deletion_started(uuid), app.close_stale_exports(date, integer, integer)
  from public, app_api, app_worker;
grant execute on function app.close_withdrawn_on(uuid), app.close_deletion_started(uuid) to app_api, app_worker;
grant execute on function app.close_stale_exports(date, integer, integer) to app_worker;

reset role;
