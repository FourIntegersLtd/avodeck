-- Down 1013: back to 1012's set-password links (support_resend_account) and MFA-only `active`.
set role app_migrator;
drop function if exists app.support_new_password(uuid);
create or replace function app.support_accounts_list()
  returns table (id uuid, person_id uuid, email text, status text, created_at timestamptz, last_sent_at timestamptz)
  language sql stable
  security definer
  set search_path = ''
as $$
  select s.id, s.person_id, p.email,
         case when exists (select 1 from app.sessions x
                           where x.person_id = s.person_id and x.mfa_verified_at is not null)
              then 'active' else 'invited' end,
         s.created_at, s.last_sent_at
  from app.support_accounts s
  join app.people p on p.id = s.person_id
  where app.is_support_staff(app.current_person_id())
  order by s.created_at desc, s.id
$$;
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
  insert into app.people (id, email, full_name, home_region) values (p_person, lower(p_email), p_full_name, p_region);
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
create function app.support_resend_account(p_id uuid)
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
  if exists (select 1 from app.sessions x where x.person_id = acct.person_id and x.mfa_verified_at is not null) then
    perform app.refuse('already_active');
  end if;
  if acct.last_sent_at > now() - interval '1 minute' then
    perform app.refuse('sent_recently');
  end if;
  update app.support_accounts s set last_sent_at = clock_timestamp() where s.id = p_id;
  return query select l.* from app.support_accounts_list() l where l.id = p_id;
end
$$;
revoke execute on function app.support_resend_account(uuid) from public, app_api, app_worker;
grant execute on function app.support_resend_account(uuid) to app_api;
reset role;
