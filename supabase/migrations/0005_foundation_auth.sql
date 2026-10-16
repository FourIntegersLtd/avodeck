-- 0005 foundation: device sessions, MFA recovery codes and email changes (architecture 4.1, 9 "Login").
--
-- * `sessions`: one row per signed-in device. The browser holds a random token in an httpOnly
--   cookie; only its SHA-256 is kept here, with the SHA-256 of the CSRF token. Supabase Auth's
--   own tokens stay server-side on the row and never reach the browser. No foreign key to
--   `people`: a session exists from the password step, before `app.ensure_person` has run.
-- * `mfa_recovery_codes`: ten one-use codes per person, kept only as SHA-256.
-- * `email_changes`: a change of sign-in address, confirmed from both addresses within a day.
-- Every table is private to its person under RLS. Looking a session up by its cookie and
-- confirming an email change by its link happen before anyone is known, so they are narrow
-- security-definer functions; so is the audit row for a failed sign-in (no person, hashed email).

set role app_migrator;

create table app.sessions (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null,
  email text not null,
  full_name text,
  token_hash text not null unique,
  csrf_hash text not null,
  device_label text not null default '' check (length(device_label) <= 120),
  aal text not null default 'aal1' check (aal in ('aal1', 'aal2')),
  mfa_enrolled boolean not null default false,
  mfa_verified_at timestamptz,
  auth_session_id uuid,
  auth_access_token text,
  auth_refresh_token text,
  auth_expires_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz,
  revoked_reason text check (revoked_reason in
    ('signed_out', 'signed_out_everywhere', 'idle', 'max_age', 'password_reset', 'mfa_verified', 'recovery'))
);
create index sessions_person on app.sessions (person_id) where revoked_at is null;

create table app.mfa_recovery_codes (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references app.people (id) on delete cascade,
  code_hash text not null,
  created_at timestamptz not null default now(),
  used_at timestamptz,
  unique (person_id, code_hash)
);

create table app.email_changes (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references app.people (id) on delete cascade,
  new_email text not null,
  old_token_hash text not null unique,
  new_token_hash text not null unique,
  old_confirmed_at timestamptz,
  new_confirmed_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '24 hours'
);
create index email_changes_person on app.email_changes (person_id);

alter table app.sessions enable row level security;
alter table app.mfa_recovery_codes enable row level security;
alter table app.email_changes enable row level security;

revoke all on app.sessions, app.mfa_recovery_codes, app.email_changes from app_worker;
revoke delete on app.sessions, app.email_changes from app_api;

create policy sessions_self on app.sessions to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());
create policy mfa_recovery_codes_self on app.mfa_recovery_codes to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());
create policy email_changes_self on app.email_changes to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

-- The session behind a cookie (by token hash). Ends it when idle longer than p_idle or older than
-- p_max; with p_touch, counts this request as activity. `state`: active, expired or revoked.
create function app.session_resolve(p_token_hash text, p_idle interval, p_max interval, p_touch boolean)
  returns table (
    id uuid, person_id uuid, email text, full_name text, csrf_hash text, aal text, mfa_enrolled boolean,
    mfa_verified_at timestamptz, last_seen_at timestamptz, auth_access_token text, auth_refresh_token text,
    auth_expires_at timestamptz, state text
  )
  language plpgsql
  security definer
  set search_path = ''
as $$
#variable_conflict use_column
declare
  s app.sessions;
  st text := 'active';
begin
  select * into s from app.sessions x where x.token_hash = p_token_hash for update;
  if not found then
    return;
  end if;
  if s.revoked_at is not null then
    st := case when s.revoked_reason in ('idle', 'max_age') then 'expired' else 'revoked' end;
  elsif s.last_seen_at < now() - p_idle or s.created_at < now() - p_max then
    update app.sessions x set revoked_at = now(),
      revoked_reason = case when s.created_at < now() - p_max then 'max_age' else 'idle' end
    where x.id = s.id;
    st := 'expired';
  elsif p_touch then
    update app.sessions x set last_seen_at = now() where x.id = s.id returning * into s;
  end if;
  return query select s.id, s.person_id, s.email, s.full_name, s.csrf_hash, s.aal, s.mfa_enrolled,
    s.mfa_verified_at, s.last_seen_at, s.auth_access_token, s.auth_refresh_token, s.auth_expires_at, st;
end
$$;

-- Confirms one of the two links of a pending email change. When both are confirmed the
-- person's sign-in address changes. Returns nothing for an unknown, used or expired link.
create function app.confirm_email_change(p_token_hash text)
  returns table (person_id uuid, new_email text, status text)
  language plpgsql
  security definer
  set search_path = ''
as $$
#variable_conflict use_column
declare
  c app.email_changes;
begin
  select * into c from app.email_changes e
  where ((e.old_token_hash = p_token_hash and e.old_confirmed_at is null)
      or (e.new_token_hash = p_token_hash and e.new_confirmed_at is null))
    and e.completed_at is null and e.cancelled_at is null and e.expires_at > now()
  for update;
  if not found then
    return;
  end if;
  update app.email_changes e set
    old_confirmed_at = case when e.old_token_hash = p_token_hash then now() else e.old_confirmed_at end,
    new_confirmed_at = case when e.new_token_hash = p_token_hash then now() else e.new_confirmed_at end
  where e.id = c.id returning * into c;
  if c.old_confirmed_at is null or c.new_confirmed_at is null then
    return query select c.person_id, c.new_email, 'waiting'::text;
    return;
  end if;
  update app.email_changes e set completed_at = now() where e.id = c.id;
  update app.people p set email = lower(c.new_email) where p.id = c.person_id;
  update app.sessions s set email = lower(c.new_email) where s.person_id = c.person_id and s.revoked_at is null;
  insert into app.audit_log (person_id, action, target)
  values (c.person_id, 'account.email_changed', 'person:' || c.person_id::text);
  return query select c.person_id, lower(c.new_email), 'completed'::text;
end
$$;

-- Audit rows written before anyone is signed in. Fixed actions only; the target is never an
-- email address (core/auth/session.py passes `email:<sha256>`).
create function app.audit_anonymous(p_action text, p_target text) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if p_action not in ('auth.sign_in_failed', 'auth.password_reset_requested') then
    raise exception using errcode = '42501', message = 'not an anonymous audit action';
  end if;
  insert into app.audit_log (action, target) values (p_action, p_target);
end
$$;

revoke execute on function app.session_resolve(text, interval, interval, boolean),
  app.confirm_email_change(text), app.audit_anonymous(text, text) from public, app_worker;
grant execute on function app.session_resolve(text, interval, interval, boolean),
  app.confirm_email_change(text), app.audit_anonymous(text, text) to app_api;

reset role;
