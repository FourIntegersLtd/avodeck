-- 0073 foundation: durable after-commit work (audit 15, BG-16).
--
-- FastAPI background tasks run after the response and are lost if the API stops between commit and task. A route
-- now writes its follow-up (an email, or a named call a slice registered) in the request's own transaction with
-- `app.defer_work`; a worker sweep takes due rows, delivers them and records the outcome, so the work happens at least
-- once. Rows are held with `for update skip locked` for the sweep's whole transaction: a crash puts them back.
--
-- * Nothing secret or personal in the clear: the payload (address, one-time code, link) is sealed in Python under the
--   cell key and bound to the row id (`SecretBox`); the table holds the template or call name, a status, counts and
--   the type name of the last error. A delivered, failed or expired row has no payload at all.
-- * A row lives at most 7 days (`expires_at`), a mail with a one-time code a lot less (the caller sets it). A finished
--   row is deleted a week after it ended (`app.prune_deferred_work`).
-- * Retry: attempt n failing waits 30 s x 2^(n-1), at most an hour; the 8th failure ends it (`failed`).
-- * The API role cannot read or write the table: only these functions. Every status change is made by them.

set role app_migrator;

create table app.deferred_work (
  id uuid primary key,
  kind text not null check (kind in ('mail', 'call')),
  name text not null check (name ~ '^[a-z][a-z0-9_.]{0,62}$'),
  sealed text check (sealed is null or (length(sealed) <= 16000 and sealed like 'av1:%')),
  requested_by uuid,
  status text not null default 'pending' check (status in ('pending', 'done', 'failed', 'expired')),
  attempts integer not null default 0 check (attempts >= 0),
  run_after timestamptz not null default now(),
  expires_at timestamptz not null,
  last_error text check (last_error is null or last_error ~ '^[A-Za-z0-9_.]{1,64}$'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz,
  check ((status = 'pending') = (sealed is not null)),
  check ((status = 'pending') = (finished_at is null))
);
create index deferred_work_due_idx on app.deferred_work (run_after) where status = 'pending';
create index deferred_work_finished_idx on app.deferred_work (finished_at) where status <> 'pending';
alter table app.deferred_work enable row level security;
revoke all on app.deferred_work from public, app_api, app_worker;

create function app.defer_work(p_id uuid, p_kind text, p_name text, p_sealed text, p_expires_at timestamptz)
  returns uuid
  language plpgsql
  security definer
  set search_path = pg_catalog
as $$
declare
  v_person uuid := app.current_person_id();
begin
  if v_person is null and app.current_job_case_id() is null then
    raise exception using errcode = '42501', message = 'deferred work needs a signed-in person or a job';
  end if;
  if p_sealed is null or p_expires_at is null or p_expires_at <= now() or p_expires_at > now() + interval '7 days' then
    raise exception using errcode = '22023', message = 'deferred work needs a payload and an expiry within 7 days';
  end if;
  if v_person is not null and (select count(*) from app.deferred_work w
       where w.requested_by = v_person and w.status = 'pending') >= 500 then
    raise exception using errcode = 'AVCAP', message = 'too much deferred work is waiting for this person';
  end if;
  insert into app.deferred_work (id, kind, name, sealed, requested_by, expires_at)
  values (p_id, p_kind, p_name, p_sealed, v_person, p_expires_at);
  return p_id;
end
$$;

-- Due rows, held until the caller's transaction ends. Rows past their expiry are ended here, unsent.
create function app.take_deferred_work(p_limit integer default 20)
  returns table (id uuid, kind text, name text, sealed text, attempts integer)
  language plpgsql
  security definer
  set search_path = pg_catalog
as $$
#variable_conflict use_column
begin
  update app.deferred_work w set status = 'expired', sealed = null, finished_at = now(), updated_at = now()
   where w.status = 'pending' and w.expires_at <= now();
  return query
    select w.id, w.kind, w.name, w.sealed, w.attempts from app.deferred_work w
     where w.status = 'pending' and w.run_after <= now()
     order by w.run_after, w.created_at
     limit least(greatest(coalesce(p_limit, 20), 1), 100)
     for update skip locked;
end
$$;

-- Records one delivery attempt. `p_error` is an exception type name; anything else in it is dropped.
create function app.finish_deferred_work(p_id uuid, p_ok boolean, p_error text) returns void
  language sql
  security definer
  set search_path = pg_catalog
as $$
  update app.deferred_work w set
    attempts = w.attempts + 1,
    status = case when p_ok then 'done' when w.attempts + 1 >= 8 then 'failed' else 'pending' end,
    sealed = case when p_ok or w.attempts + 1 >= 8 then null else w.sealed end,
    finished_at = case when p_ok or w.attempts + 1 >= 8 then now() end,
    last_error = case when p_ok then null
      else nullif(left(regexp_replace(coalesce(p_error, ''), '[^A-Za-z0-9_.]', '', 'g'), 64), '') end,
    run_after = case when p_ok then w.run_after
      else now() + least(interval '1 hour', interval '30 seconds' * power(2, w.attempts)) end,
    updated_at = now()
  where w.id = p_id and w.status = 'pending'
$$;

create function app.prune_deferred_work(p_older_than_days integer default 7) returns integer
  language plpgsql
  security definer
  set search_path = pg_catalog
as $$
declare
  n integer;
begin
  delete from app.deferred_work w
   where w.status <> 'pending' and w.finished_at < now() - make_interval(days => greatest(p_older_than_days, 0));
  get diagnostics n = row_count;
  return n;
end
$$;

revoke execute on function app.defer_work(uuid, text, text, text, timestamptz),
  app.take_deferred_work(integer), app.finish_deferred_work(uuid, boolean, text), app.prune_deferred_work(integer)
  from public, app_api, app_worker;
grant execute on function app.defer_work(uuid, text, text, text, timestamptz) to app_api, app_worker;
grant execute on function app.take_deferred_work(integer), app.finish_deferred_work(uuid, boolean, text),
  app.prune_deferred_work(integer) to app_worker;

reset role;
