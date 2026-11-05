-- 0056 foundation: the slot scheduler counts the whole account, not one case.
--
-- `ai_request_slots` is worker-only and its policy (0049) lets a job see only its own case's rows. The
-- kit's two counts (grants in the last window, calls in flight) therefore saw only the current case, so
-- five bundles each thought they were alone and AWS returned 429. `app.ai_slots_in_use` counts every row
-- of one provider and returns two numbers only: no row, case or job id leaves it, and the table's policy
-- is unchanged.
--
-- * `in_flight` (column): the kit sets it when it grants a slot and clears it when the call has answered or
--   failed. A call that never reported back (a crashed worker) stops counting after ten minutes.
-- * `in_flight` (function result): slots flagged in flight, granted within ten minutes.
-- * `granted_in_window`: granted within `p_window` (the requests-per-minute count).

set role app_migrator;

alter table app.ai_request_slots add column in_flight boolean not null default false;

create index ai_request_slots_in_flight on app.ai_request_slots (provider, granted_at) where in_flight;

create function app.ai_slots_in_use(p_provider text, p_window interval)
  returns table (in_flight int, granted_in_window int)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select
    (count(*) filter (where s.in_flight and s.granted_at > now() - interval '10 minutes'))::int,
    (count(*) filter (where s.granted_at > now() - p_window))::int
  from app.ai_request_slots s
  where s.provider = p_provider
$$;

revoke all on function app.ai_slots_in_use(text, interval) from public, app_api;
grant execute on function app.ai_slots_in_use(text, interval) to app_worker;

reset role;
