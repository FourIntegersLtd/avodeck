-- 0022 foundation: a case on hold keeps its jobs and messages back, case member names, when a case
-- closed and who asked for a job (requests from slices C and H; state-machines.md "On hold").
--
-- * `app.job_waits_for_hold(job, runs_on_hold)`: the worker asks before it runs a job. A job of a
--   case on hold stays `queued` with `error_code` `case_on_hold` (unless its kind deals with the
--   hold itself). It waits for the case's row lock, so a resume in progress is seen once committed.
-- * `notifications.held_at`: a message about a case on hold is kept back (not listed, not emailed at
--   once, not in the daily email) unless it is about the hold itself (`p_while_on_hold`).
-- * `app.release_case_hold(case)`: once the case is off hold, by someone who may resume it
--   (`case.hold`) or the case's own job. Puts the held jobs back on the queue, lists the held
--   messages again and returns the emails to send at once; the rest go in the daily email.
-- * `cases.closed_at`: set by the database when a case is settled or withdrawn, kept when it is
--   archived or deleted, cleared when it is reopened. Nobody writes it by hand.
-- * `jobs.requested_by`: the signed-in person who queued the job; none for a job queued by a job
--   or the daily run. The database takes it from the request; nobody sets it by hand.
-- * `app.case_member_names(case)`: the named expert's and current case members' display names, for
--   anyone who may see the case (`case.view_metadata`) or the case's own job; nothing otherwise.

set role app_migrator;

-- Messages kept back -----------------------------------------------------------------------------
alter table app.notifications add column held_at timestamptz;
create index notifications_held on app.notifications (case_id) where held_at is not null;

drop function app.notify(uuid, uuid, uuid, text);

create function app.notify(p_person uuid, p_org uuid, p_case uuid, p_kind text, p_while_on_hold boolean default false)
  returns table (notification_id uuid, email text)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case_org uuid;
  v_on_hold boolean := false;
  v_held boolean;
  v_app app.notification_preferences;
  v_mail app.notification_preferences;
  v_email text;
  v_bounced boolean;
  v_in_app boolean;
  v_now boolean;
  v_daily boolean;
  v_id uuid;
begin
  if p_kind is null or p_kind !~ '^[a-z][a-z0-9_.]{0,63}$' then
    raise exception 'a message kind is a short code' using errcode = '23514';
  end if;
  -- Who is telling.
  if app.current_job_case_id() is not null then
    if p_case is distinct from app.current_job_case_id() then
      raise exception 'a job tells people only about its own case' using errcode = '42501';
    end if;
  elsif app.current_person_id() is not null then
    if p_case is not null then
      if not app.can_access_case(p_case, 'case.view_metadata') then
        raise exception 'not allowed' using errcode = '42501';
      end if;
    elsif not (p_org = any (app.my_org_ids())) then
      raise exception 'not allowed' using errcode = '42501';
    end if;
  else
    raise exception 'not allowed' using errcode = '42501';
  end if;
  -- Who is told.
  if p_case is not null then
    select c.organisation_id, c.on_hold into v_case_org, v_on_hold from app.cases c where c.id = p_case for share;
    if v_case_org is distinct from p_org then
      raise exception 'the case is not in that organisation' using errcode = '22023';
    end if;
  end if;
  if not exists (
    select 1 from app.memberships m
    where m.organisation_id = p_org and m.person_id = p_person and m.ended_at is null
  ) then
    raise exception 'that person is not in the organisation' using errcode = '22023';
  end if;
  -- Their choices for this organisation (none saved: in the app and by email at once).
  select * into v_app from app.notification_preferences p
    where p.person_id = p_person and p.organisation_id = p_org and p.channel = 'in_app';
  select * into v_mail from app.notification_preferences p
    where p.person_id = p_person and p.organisation_id = p_org and p.channel = 'email';
  if p_kind = any (coalesce(v_app.muted_kinds, '{}') || coalesce(v_mail.muted_kinds, '{}')) then
    return;
  end if;
  select pe.email, pe.email_bounced_at is not null into v_email, v_bounced from app.people pe where pe.id = p_person;
  v_in_app := coalesce(v_app.enabled, true);
  v_daily := coalesce(v_mail.digest, false) and not v_bounced;
  v_now := coalesce(v_mail.enabled, true) and not coalesce(v_mail.digest, false) and not v_bounced;
  if not (v_in_app or v_now or v_daily) then
    return;
  end if;
  v_held := coalesce(v_on_hold, false) and not coalesce(p_while_on_hold, false);
  insert into app.notifications (person_id, organisation_id, case_id, kind, in_app, emailed_at, held_at)
    values (p_person, p_org, p_case, p_kind, v_in_app,
            case when v_now and not v_held then now() end, case when v_held then now() end)
    returning id into v_id;
  notification_id := v_id;
  email := case when v_now and not v_held then v_email end;
  return next;
end
$$;

create or replace function app.notifications_daily(p_run_date date)
  returns table (email text, message_count integer)
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  delete from app.notifications n where n.created_at < (p_run_date - interval '1 year');
  return query
  with due as (
    update app.notifications n set emailed_at = now()
    from app.notification_preferences p, app.people pe
    where n.emailed_at is null
      and n.held_at is null
      and n.created_at < (p_run_date + 1)
      and p.person_id = n.person_id and p.organisation_id = n.organisation_id
      and p.channel = 'email' and p.digest
      and pe.id = n.person_id and pe.email_bounced_at is null
    returning n.person_id
  )
  select pe.email, count(*)::integer
  from due join app.people pe on pe.id = due.person_id
  group by pe.email;
end
$$;

-- Jobs kept back ---------------------------------------------------------------------------------
create function app.job_waits_for_hold(p_job uuid, p_runs_on_hold boolean) returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case uuid;
  v_on_hold boolean;
begin
  select j.case_id into v_case from app.jobs j where j.id = p_job;
  if v_case is null then
    return false;
  end if;
  if v_case is distinct from app.current_job_case_id() then
    raise exception 'a job asks only about its own case' using errcode = '42501';
  end if;
  -- Waits for a hold or resume in progress, then reads what it committed.
  select c.on_hold into v_on_hold from app.cases c where c.id = v_case for share;
  if not coalesce(v_on_hold, false) or p_runs_on_hold then
    return false;
  end if;
  update app.jobs set error_code = 'case_on_hold' where id = p_job and status = 'queued';
  return true;
end
$$;

create function app.release_case_hold(p_case uuid) returns table (email text, kind text)
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_on_hold boolean;
  v_job record;
  v_last record;
begin
  if not (coalesce(app.current_job_case_id() = p_case, false)
          or coalesce(app.can_access_case(p_case, 'case.hold'), false)) then
    raise exception using errcode = '42501', message = 'releasing a hold needs case.hold';
  end if;
  select c.on_hold into v_on_hold from app.cases c where c.id = p_case for update;
  if v_on_hold then
    perform app.refuse('the case is still on hold');
  end if;
  for v_job in
    select j.id, j.priority from app.jobs j
    where j.case_id = p_case and j.status = 'queued' and j.error_code = 'case_on_hold'
    order by j.created_at
    for update
  loop
    select q.queue_name, q.task_name, q.lock into v_last
      from procrastinate.procrastinate_jobs q
      where q.args ->> 'job_id' = v_job.id::text
      order by q.id desc limit 1;
    update app.jobs set error_code = null where id = v_job.id;
    perform procrastinate_defer_jobs_v1(array[row(
      coalesce(v_last.queue_name, 'jobs'), coalesce(v_last.task_name, 'run_job'), v_job.priority,
      case when v_last.queue_name is null then 'case:' || p_case::text else v_last.lock end,
      null, jsonb_build_object('job_id', v_job.id, 'case_id', p_case), null
    )::procrastinate_job_to_defer_v1]);
  end loop;
  return query
  with released as (
    update app.notifications n set held_at = null,
      emailed_at = case when sends.now then pg_catalog.now() end
    from (
      select h.id,
             coalesce(p.enabled, true) and not coalesce(p.digest, false) and pe.email_bounced_at is null as now
      from app.notifications h
      join app.people pe on pe.id = h.person_id
      left join app.notification_preferences p
        on p.person_id = h.person_id and p.organisation_id = h.organisation_id and p.channel = 'email'
      where h.case_id = p_case and h.held_at is not null
    ) sends
    where n.id = sends.id
    returning n.person_id, n.kind, sends.now
  )
  select pe.email, r.kind from released r join app.people pe on pe.id = r.person_id where r.now;
end
$$;

-- When a case closed -----------------------------------------------------------------------------
alter table app.cases add column closed_at timestamptz;

create function app.cases_closed_at() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.closed_at := null;
  elsif new.status is distinct from old.status and new.status in ('settled', 'withdrawn') then
    new.closed_at := now();
  elsif new.status is distinct from old.status and new.status = 'active' then
    new.closed_at := null;
  else
    new.closed_at := old.closed_at;
  end if;
  return new;
end
$$;

update app.cases c set closed_at = coalesce(
  c.settled_at,
  (select max(a.at) from app.audit_log a where a.case_id = c.id and a.action = 'case.withdrawn'),
  case when c.status = 'withdrawn' then now() end)
where c.status in ('settled', 'withdrawn', 'archived', 'deleted');

-- Named to run after cases_actor and cases_guard (triggers fire in name order).
create trigger cases_closed_at before insert or update on app.cases
  for each row execute function app.cases_closed_at();

-- Who asked for a job ----------------------------------------------------------------------------
alter table app.jobs add column requested_by uuid references app.people (id) on delete set null;

create function app.jobs_requested_by() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  -- The app's own roles (directly or inside its functions) never choose it; local test set-up may.
  if current_user in ('app_api', 'app_worker', 'app_migrator') then
    if tg_op = 'INSERT' then
      new.requested_by := app.current_person_id();
    else
      new.requested_by := old.requested_by;
    end if;
  end if;
  return new;
end
$$;
create trigger jobs_requested_by before insert or update on app.jobs
  for each row execute function app.jobs_requested_by();

-- Case member names ------------------------------------------------------------------------------
create function app.case_member_names(p_case uuid) returns table (person_id uuid, display_name text)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select pe.id, pe.full_name
  from app.people pe
  where (coalesce(app.current_job_case_id() = p_case, false)
         or coalesce(app.can_access_case(p_case, 'case.view_metadata'), false))
    and (pe.id = (select c.named_expert_id from app.cases c where c.id = p_case)
         or exists (select 1 from app.case_members m
                    where m.case_id = p_case and m.person_id = pe.id and m.removed_at is null))
$$;

revoke execute on function app.notify(uuid, uuid, uuid, text, boolean), app.job_waits_for_hold(uuid, boolean),
  app.release_case_hold(uuid), app.cases_closed_at(), app.jobs_requested_by(), app.case_member_names(uuid)
  from public, app_api, app_worker;
grant execute on function app.notify(uuid, uuid, uuid, text, boolean) to app_api, app_worker;
grant execute on function app.job_waits_for_hold(uuid, boolean) to app_worker;
grant execute on function app.release_case_hold(uuid) to app_api, app_worker;
grant execute on function app.case_member_names(uuid) to app_api, app_worker;

reset role;
