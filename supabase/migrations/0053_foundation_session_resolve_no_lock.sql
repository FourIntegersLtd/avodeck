-- 0053 foundation: looking up the session no longer holds its row locked for the whole request.
--
-- The session lookup now shares the request's transaction (one database trip fewer, core/db.py
-- session_request). app.session_resolve used `select ... for update` and always updated last_seen_at, so the
-- lock on the session row would have lasted until the route finished: one browser's parallel requests
-- would have run one after another. Now:
--   the lookup takes no lock;
--   activity is counted at most every 30 seconds (the idle limit is 30 minutes), and only if the row is not
--   already locked by another request (`skip locked`): that request is counting it;
--   ending an idle or expired session still updates the row (rare, and committed even when the answer is 401).

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
  v_seen timestamptz;
begin
  select * into s from app.sessions x where x.token_hash = p_token_hash;
  if not found then
    return;
  end if;
  if s.revoked_at is not null then
    st := case when s.revoked_reason in ('idle', 'max_age') then 'expired' else 'revoked' end;
  elsif s.last_seen_at < now() - p_idle or s.created_at < now() - p_max then
    update app.sessions x set revoked_at = now(),
      revoked_reason = case when s.created_at < now() - p_max then 'max_age' else 'idle' end
    where x.id = s.id and x.revoked_at is null;
    st := 'expired';
  elsif p_touch and s.last_seen_at < now() - interval '30 seconds' then
    update app.sessions x set last_seen_at = now()
    where x.id in (select y.id from app.sessions y where y.id = s.id and y.revoked_at is null for update skip locked)
    returning x.last_seen_at into v_seen;
    if v_seen is not null then
      s.last_seen_at := v_seen;
    end if;
  end if;
  select p.must_change_password into v_must from app.people p where p.id = s.person_id;
  return query select s.id, s.person_id, s.email, s.full_name, s.csrf_hash, s.aal, s.mfa_enrolled,
    s.mfa_verified_at, s.last_seen_at, s.auth_access_token, s.auth_refresh_token, s.auth_expires_at,
    coalesce(v_must, false), st;
end
$$;

reset role;
