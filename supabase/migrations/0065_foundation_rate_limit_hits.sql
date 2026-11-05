-- 0065 foundation: per-person, per-endpoint request counts (scalability PR 12).
--
-- app.rate_limit_hits         one row per person and endpoint (the route's path template with its method, never
--                             an id or any text from the request): the minute it counts and the requests seen
--                             in it. The table is closed to the app roles; only app.rate_limit_hit() counts.
-- app.rate_limit_peek(endpoint) reads the count so far for the signed-in person in the current minute and
--                             the seconds left in it, taking no lock. The API looks first, at the start of a
--                             request, and turns the person away when the count has reached
--                             API_RATE_PER_MINUTE (429 `slow_down` with Retry-After).
-- app.rate_limit_hit(endpoint) counts one request for the signed-in person in the current minute and answers
--                             the count so far and the seconds left in the minute. One upsert, run by the API
--                             in a short transaction of its own after the request has ended (never inside the
--                             request's: its row lock would make two requests of one person to one endpoint
--                             wait for each other, such as the parts of an upload). A new minute starts the
--                             count again at one, so the table stays one row per person and endpoint and
--                             needs no clean-up. Answers nothing when nobody known is signed in.
-- The rows go with the person (on delete cascade).

set role app_migrator;

create table app.rate_limit_hits (
  person_id uuid not null references app.people (id) on delete cascade,
  endpoint text not null check (length(endpoint) between 1 and 200),
  window_start timestamptz not null default date_trunc('minute', now()),
  hits integer not null default 1 check (hits >= 1),
  created_at timestamptz not null default now(),
  primary key (person_id, endpoint)
) with (fillfactor = 70);  -- room for the update-in-place on every request (heap-only tuples)

alter table app.rate_limit_hits enable row level security;
revoke all on app.rate_limit_hits from app_api, app_worker;

create function app.rate_limit_hit(p_endpoint text)
  returns table (hits integer, retry_after integer)
  language sql
  volatile
  security definer
  set search_path = ''
as $$
  insert into app.rate_limit_hits as r (person_id, endpoint, window_start, hits)
  select app.current_person_id(), p_endpoint, pg_catalog.date_trunc('minute', pg_catalog.now()), 1
  where exists (select 1 from app.people p where p.id = app.current_person_id())
  on conflict (person_id, endpoint) do update
    set hits = case when r.window_start = excluded.window_start then r.hits + 1 else 1 end,
        window_start = excluded.window_start
  returning r.hits,
    greatest(1, pg_catalog.ceil(extract(epoch from (r.window_start + interval '1 minute' - pg_catalog.now())))::integer)
$$;

create function app.rate_limit_peek(p_endpoint text)
  returns table (hits integer, retry_after integer)
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select r.hits,
    greatest(1, pg_catalog.ceil(extract(epoch from (r.window_start + interval '1 minute' - pg_catalog.now())))::integer)
  from app.rate_limit_hits r
  where r.person_id = app.current_person_id()
    and r.endpoint = p_endpoint
    and r.window_start = pg_catalog.date_trunc('minute', pg_catalog.now())
$$;

revoke execute on function app.rate_limit_hit(text), app.rate_limit_peek(text) from public, app_worker;
grant execute on function app.rate_limit_hit(text), app.rate_limit_peek(text) to app_api;

reset role;
