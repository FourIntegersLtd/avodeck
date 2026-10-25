-- 1012 organisations: accounts Avodeck support creates for a new person (permissions.md "Avodeck staff").
--
-- support_accounts                 one row per account a support staff member created: the person, the
--                                  staff member, and when the set-password email was last sent. No name,
--                                  no password, no team, no case data. `invited` until the person first
--                                  completes MFA in a session, then `active` (derived from app.sessions).
--                                  Support adds nobody to a team: a team's own owners and admins invite.
-- The running app never reads or writes the table directly; current support staff do, through:
-- app.support_account_precheck(email)
--                                  waits on the staff member's own lock (so two creates at once cannot both
--                                  pass the cap), then refuses (AV409) too_many_accounts (20 an hour per
--                                  staff member, counting accounts made and refused attempts audited as
--                                  `support.account_refused`) and email_taken. Run before the Auth login.
-- app.support_create_account(person, email, full_name, region)
--                                  the same checks, then the people row (id = the new Auth user), the
--                                  person's own practice with them as owner and the support_accounts row.
-- app.support_resend_account(id)   refuses account_not_found, already_active, sent_recently (within a
--                                  minute); marks the link sent again. Returns the account as listed.
-- app.support_accounts_list()      every account support created, newest first; nothing for anyone else.
-- Non-staff calling the writers get 42501. The worker may run none of these.

set role app_migrator;

create table app.support_accounts (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null unique references app.people (id),
  created_by uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  last_sent_at timestamptz not null default now()
);

create index support_accounts_created_by on app.support_accounts (created_by, created_at);

alter table app.support_accounts enable row level security;
revoke all on app.support_accounts from app_api, app_worker;

create function app.support_account_check(p_email text) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  v_since timestamptz;
  v_count bigint;
  r record;
begin
  if not app.is_support_staff(me) then
    raise exception using errcode = '42501', message = 'support staff only';
  end if;
  -- Held to the end of the request's transaction: this staff member's next create waits, then counts.
  perform pg_advisory_xact_lock(hashtextextended('support_accounts:' || me::text, 0));
  v_since := clock_timestamp() - interval '1 hour';
  select count(*) into v_count from app.support_accounts s where s.created_by = me and s.created_at > v_since;
  -- Refused attempts, newest first along the audit chain, stopping once past the hour (with a little slack
  -- for rows written slightly out of order); audit_log has no index on person or action.
  for r in select a.person_id, a.action, a.at from app.audit_log a order by a.seq desc loop
    exit when r.at < v_since - interval '5 minutes';
    if r.person_id = me and r.action = 'support.account_refused' and r.at > v_since then
      v_count := v_count + 1;
    end if;
  end loop;
  if v_count >= 20 then
    perform app.refuse('too_many_accounts');
  end if;
  if exists (select 1 from app.people p where lower(p.email) = lower(p_email)) then
    perform app.refuse('email_taken');
  end if;
end
$$;

create function app.support_account_precheck(p_email text) returns void
  language sql
  security definer
  set search_path = ''
as $$
  select app.support_account_check(p_email)
$$;

create function app.support_create_account(p_person uuid, p_email text, p_full_name text, p_region text)
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

create function app.support_accounts_list()
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

revoke execute on function
  app.support_account_check(text),
  app.support_account_precheck(text),
  app.support_create_account(uuid, text, text, text),
  app.support_accounts_list(),
  app.support_resend_account(uuid)
from public, app_api, app_worker;
grant execute on function
  app.support_account_precheck(text),
  app.support_create_account(uuid, text, text, text),
  app.support_accounts_list(),
  app.support_resend_account(uuid)
to app_api;

reset role;
