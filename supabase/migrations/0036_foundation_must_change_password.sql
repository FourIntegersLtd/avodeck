-- 0036 foundation: a person who must choose their own password (ADR 0024; threat model T27).
--
-- people.must_change_password      true while the person signs in with a temporary password Avodeck support
--                                  gave them. FastAPI then refuses every signed-in route but the few that
--                                  lead to choosing their own (403 `password_change_required`, core/http.py).
--                                  Only security-definer functions change it: app.password_changed() (the
--                                  person, after a new password) and support's account functions (1013). A
--                                  trigger refuses a direct write by the app roles (42501).
-- app.session_resolve()            also returns must_change_password, before `state`.
-- sessions.revoked_reason          gains 'password_changed' (the person's other devices) and
--                                  'password_renewed' (support made a new temporary password).
-- app.password_change_attempts     wrong current passwords at `POST /v1/auth/password/change` in the current
--                                  15-minute window (five lock it until the window ends), like
--                                  mfa_attempts (0014). The person's own row; the app cannot delete it.

set role app_migrator;

alter table app.people add column must_change_password boolean not null default false;

create function app.people_guard_must_change_password() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.must_change_password is distinct from old.must_change_password
     and current_user in ('app_api', 'app_worker') then
    raise exception using errcode = '42501', message = 'must_change_password changes only through its functions';
  end if;
  return new;
end
$$;

create trigger people_guard_must_change_password before update of must_change_password on app.people
  for each row execute function app.people_guard_must_change_password();

-- Clears the signed-in person's own flag, after FastAPI has set their new password in Supabase Auth.
create function app.password_changed() returns void
  language sql
  security definer
  set search_path = ''
as $$
  update app.people set must_change_password = false where id = app.current_person_id()
$$;

alter table app.sessions drop constraint sessions_revoked_reason_check;
alter table app.sessions add constraint sessions_revoked_reason_check check (revoked_reason in
  ('signed_out', 'signed_out_everywhere', 'idle', 'max_age', 'password_reset', 'mfa_verified', 'recovery',
   'password_changed', 'password_renewed'));

drop function app.session_resolve(text, interval, interval, boolean);

-- The session behind a cookie (by token hash). Ends it when idle longer than p_idle or older than
-- p_max; with p_touch, counts this request as activity. `state`: active, expired or revoked.
create function app.session_resolve(p_token_hash text, p_idle interval, p_max interval, p_touch boolean)
  returns table (
    id uuid, person_id uuid, email text, full_name text, csrf_hash text, aal text, mfa_enrolled boolean,
    mfa_verified_at timestamptz, last_seen_at timestamptz, auth_access_token text, auth_refresh_token text,
    auth_expires_at timestamptz, must_change_password boolean, state text
  )
  language plpgsql
  security definer
  set search_path = ''
as $$
#variable_conflict use_column
declare
  s app.sessions;
  st text := 'active';
  v_must boolean;
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
  select p.must_change_password into v_must from app.people p where p.id = s.person_id;
  return query select s.id, s.person_id, s.email, s.full_name, s.csrf_hash, s.aal, s.mfa_enrolled,
    s.mfa_verified_at, s.last_seen_at, s.auth_access_token, s.auth_refresh_token, s.auth_expires_at,
    coalesce(v_must, false), st;
end
$$;

revoke execute on function app.session_resolve(text, interval, interval, boolean), app.password_changed(),
  app.people_guard_must_change_password() from public, app_worker;
revoke execute on function app.people_guard_must_change_password() from app_api;
grant execute on function app.session_resolve(text, interval, interval, boolean), app.password_changed() to app_api;


create table app.password_change_attempts (
  person_id uuid primary key references app.people (id) on delete cascade,
  failures integer not null default 0 check (failures >= 0),
  window_started_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table app.password_change_attempts enable row level security;
revoke all on app.password_change_attempts from app_worker;
revoke delete on app.password_change_attempts from app_api;
create policy password_change_attempts_self on app.password_change_attempts to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

reset role;
