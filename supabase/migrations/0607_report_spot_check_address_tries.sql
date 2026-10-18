-- 0607 report: spot-check links are limited per address as well as per link (threat model T9).
-- spot_check_address_tries: how many times one address has tried any spot-check link in the current window.
--   The address is kept only as a hash and the row is deleted a day after its window starts. No role reads or
--   writes the table directly; the API counts a try through spot_check_address_try.
-- spot_check_address_try: counts one try and says whether the address is still within the limit.

set role app_migrator;

create table app.spot_check_address_tries (
  address_hash text primary key check (address_hash ~ '^[0-9a-f]{64}$'),
  tries integer not null default 0 check (tries >= 0),
  window_started_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table app.spot_check_address_tries enable row level security;
revoke all on app.spot_check_address_tries from app_api, app_worker;

create function app.spot_check_address_try(p_address_hash text, p_limit integer, p_window interval)
  returns boolean
  language plpgsql
  volatile
  security definer
  set search_path = ''
as $$
declare
  v_tries integer;
begin
  delete from app.spot_check_address_tries where window_started_at < now() - interval '1 day';
  insert into app.spot_check_address_tries as t (address_hash, tries) values (p_address_hash, 1)
  on conflict (address_hash) do update set
    tries = case when t.window_started_at > now() - p_window then t.tries + 1 else 1 end,
    window_started_at = case when t.window_started_at > now() - p_window then t.window_started_at else now() end
  returning tries into v_tries;
  return v_tries <= p_limit;
end;
$$;

revoke all on function app.spot_check_address_try(text, integer, interval) from public;
grant execute on function app.spot_check_address_try(text, integer, interval) to app_api;

reset role;
