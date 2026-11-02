-- 0054 foundation: counting session activity is its own short statement, after the request.
--
-- 0053 counted activity inside app.session_resolve, so the session row stayed locked until the request's
-- transaction ended: sign-out or MFA verify could wait behind a long upload. The request now looks the
-- session up without counting (p_touch false) and, once its transaction has ended, calls this function as a
-- single autocommit statement: the row lock lasts only for that statement.

set role app_migrator;

create or replace function app.session_touch(p_id uuid, p_idle interval, p_max interval)
  returns void
  language sql
  security definer
  set search_path = ''
as $$
  update app.sessions x set last_seen_at = now()
  where x.id = p_id and x.revoked_at is null and x.last_seen_at < now() - interval '30 seconds'
    and x.last_seen_at >= now() - p_idle and x.created_at >= now() - p_max  -- never revives an expired session
$$;

revoke execute on function app.session_touch(uuid, interval, interval) from public, app_worker;
grant execute on function app.session_touch(uuid, interval, interval) to app_api;

reset role;
