-- 0009 foundation: messages (in the app and by email), message choices and the bounce flag
-- (architecture 5.2, 10.9; behaviour index `notifications`; failure mode F33).
--
-- * `notification_preferences.enabled`: a channel can be switched off per organisation.
-- * `notifications.in_app`: false when the person only wants email; such rows are not listed.
-- * `notifications.kind`: a short code; the words come from the web app's language files. Emails
--   carry only the template and the kind, never a case name or case text.
-- * `people.email_bounced_at`: set when mail to the address bounced; no email is sent until the
--   address changes (which clears it). The web app shows a banner instead (F33).
-- * `app.notify(...)`: the only way to create a message. A signed-in person may tell someone about
--   a case they can see (or about their own organisation); a job only about its own case. The person
--   told must be a current member of that organisation, and a case must belong to it.
-- * `app.notifications_daily(run_date)`: worker only. Returns one row per "one email a day" person
--   with the count of messages not yet emailed, marks them emailed, and deletes messages over a
--   year old.
-- * `app.mark_email_bounced(email)`: the email service's bounce report.

set role app_migrator;

alter table app.notification_preferences add column enabled boolean not null default true;
alter table app.notifications add column in_app boolean not null default true;
alter table app.notifications add constraint notifications_kind_code check (kind ~ '^[a-z][a-z0-9_.]{0,63}$');
alter table app.people add column email_bounced_at timestamptz;
create index notifications_to_email on app.notifications (person_id) where emailed_at is null;

-- A new address has not bounced.
create function app.people_email_changed() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.email is distinct from old.email then
    new.email_bounced_at := null;
  end if;
  return new;
end
$$;
create trigger people_email_changed before update of email on app.people
  for each row execute function app.people_email_changed();

-- People may only mark their own messages read.
revoke update on app.notifications from app_api;
grant update (read_at) on app.notifications to app_api;

create function app.notify(p_person uuid, p_org uuid, p_case uuid, p_kind text)
  returns table (notification_id uuid, email text)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case_org uuid;
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
    select c.organisation_id into v_case_org from app.cases c where c.id = p_case;
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
  insert into app.notifications (person_id, organisation_id, case_id, kind, in_app, emailed_at)
    values (p_person, p_org, p_case, p_kind, v_in_app, case when v_now then now() end)
    returning id into v_id;
  notification_id := v_id;
  email := case when v_now then v_email end;
  return next;
end
$$;

create function app.notifications_daily(p_run_date date)
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

create function app.mark_email_bounced(p_email text) returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  update app.people set email_bounced_at = coalesce(email_bounced_at, now()) where lower(email) = lower(p_email);
  return found;
end
$$;

revoke execute on function app.notify(uuid, uuid, uuid, text), app.notifications_daily(date),
  app.mark_email_bounced(text), app.people_email_changed() from public, app_api, app_worker;
grant execute on function app.notify(uuid, uuid, uuid, text) to app_api, app_worker;
grant execute on function app.notifications_daily(date) to app_worker;
grant execute on function app.mark_email_bounced(text) to app_api;

reset role;
