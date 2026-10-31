-- 1013 organisations: support gives a new person a temporary password (ADR 0024; reverses the
-- "support never sees the password" part of 1012 and threat model T27).
--
-- FastAPI makes the temporary password and sets it on the Supabase Auth login; it is never stored here.
-- app.support_create_account(...)  as 1012, and the new person must change their password
--                                  (people.must_change_password, 0036).
-- app.support_accounts_list()      `active` once the person has chosen their own password and completed
--                                  MFA in a session; `invited` until then.
-- app.support_new_password(id)     replaces 1012's support_resend_account: refuses account_not_found,
--                                  already_active (the person has their own password or has set up MFA,
--                                  whatever the list says), sent_recently (within a minute); marks the time, sets
--                                  must_change_password again and signs the person out everywhere
--                                  ('password_renewed'). Returns the account as listed. FastAPI then sets
--                                  the new temporary password on the login.

set role app_migrator;

create or replace function app.support_create_account(p_person uuid, p_email text, p_full_name text, p_region text)
  returns uuid
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  v_personal uuid;
  v_id uuid;
begin
  perform app.support_account_check(p_email);
  insert into app.people (id, email, full_name, home_region, must_change_password)
  values (p_person, lower(p_email), p_full_name, p_region, true);
  insert into app.organisations (name, kind, region)
  values (coalesce(nullif(p_full_name, ''), lower(p_email)), 'personal', p_region)
  returning id into v_personal;
  insert into app.memberships (organisation_id, person_id, role) values (v_personal, p_person, 'owner');
  insert into app.audit_log (person_id, organisation_id, action, target)
  values (me, v_personal, 'person.created', 'person:' || p_person::text);
  insert into app.support_accounts (person_id, created_by) values (p_person, me) returning id into v_id;
  return v_id;
end
$$;

create or replace function app.support_accounts_list()
  returns table (id uuid, person_id uuid, email text, status text, created_at timestamptz, last_sent_at timestamptz)
  language sql stable
  security definer
  set search_path = ''
as $$
  select s.id, s.person_id, p.email,
         case when not p.must_change_password
                   and exists (select 1 from app.sessions x
                               where x.person_id = s.person_id and x.mfa_verified_at is not null)
              then 'active' else 'invited' end,
         s.created_at, s.last_sent_at
  from app.support_accounts s
  join app.people p on p.id = s.person_id
  where app.is_support_staff(app.current_person_id())
  order by s.created_at desc, s.id
$$;

drop function app.support_resend_account(uuid);

create function app.support_new_password(p_id uuid)
  returns table (id uuid, person_id uuid, email text, status text, created_at timestamptz, last_sent_at timestamptz)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  acct app.support_accounts;
begin
  if not app.is_support_staff(me) then
    raise exception using errcode = '42501', message = 'support staff only';
  end if;
  select * into acct from app.support_accounts s where s.id = p_id for update;
  if not found then
    perform app.refuse('account_not_found');
  end if;
  -- A password of the person's own (changed, or reset from the email link) or MFA set up: never replaced,
  -- even while the list still says invited (support must not take over the account).
  if not (select p.must_change_password from app.people p where p.id = acct.person_id)
     or exists (select 1 from app.sessions x
                where x.person_id = acct.person_id and (x.mfa_enrolled or x.mfa_verified_at is not null)) then
    perform app.refuse('already_active');
  end if;
  if acct.last_sent_at > now() - interval '1 minute' then
    perform app.refuse('sent_recently');
  end if;
  update app.support_accounts s set last_sent_at = clock_timestamp() where s.id = p_id;
  update app.people p set must_change_password = true where p.id = acct.person_id;
  update app.sessions x set revoked_at = now(), revoked_reason = 'password_renewed'
  where x.person_id = acct.person_id and x.revoked_at is null;
  return query select l.* from app.support_accounts_list() l where l.id = p_id;
end
$$;

revoke execute on function app.support_new_password(uuid) from public, app_api, app_worker;
grant execute on function app.support_new_password(uuid) to app_api;

reset role;
