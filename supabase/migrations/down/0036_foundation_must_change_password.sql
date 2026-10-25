-- Down 0036: back to sessions without must_change_password (support's 1013 must be undone first).
set role app_migrator;
drop table if exists app.password_change_attempts;
drop function if exists app.password_changed();
drop function app.session_resolve(text, interval, interval, boolean);
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
revoke execute on function app.session_resolve(text, interval, interval, boolean) from public, app_worker;
grant execute on function app.session_resolve(text, interval, interval, boolean) to app_api;
update app.sessions set revoked_reason = 'signed_out_everywhere'
  where revoked_reason in ('password_changed', 'password_renewed');
alter table app.sessions drop constraint sessions_revoked_reason_check;
alter table app.sessions add constraint sessions_revoked_reason_check check (revoked_reason in
  ('signed_out', 'signed_out_everywhere', 'idle', 'max_age', 'password_reset', 'mfa_verified', 'recovery'));
drop trigger if exists people_guard_must_change_password on app.people;
drop function if exists app.people_guard_must_change_password();
alter table app.people drop column if exists must_change_password;
reset role;
