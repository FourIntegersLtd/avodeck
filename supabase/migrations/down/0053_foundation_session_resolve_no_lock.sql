-- Down 0053: the session lookup locks the row again (0036).
set role app_migrator;

create or replace function app.session_resolve(p_token_hash text, p_idle interval, p_max interval, p_touch boolean)
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

reset role;
