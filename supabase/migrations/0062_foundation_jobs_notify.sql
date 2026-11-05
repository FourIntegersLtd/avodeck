-- 0062 foundation: a job change tells the API which case moved (live updates pushed, not polled; scalability PR 8).
--
-- The API holds one LISTEN connection and wakes the open streams of a case when its jobs change, instead of every
-- stream looking at the database once a second. The notification carries the case id and nothing else (rule 6):
-- a uuid, never a step name, a count, a status or any text. A stream that wakes reads the state itself, as the
-- signed-in person under row-level security (rule 9), so no access decision is made here and RLS is not widened.
-- Only the columns a stream shows fire it; a change to attempts or the queue's own bookkeeping does not.

set role app_migrator;

create function app.jobs_notify()
  returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if new.case_id is not null then
    perform pg_catalog.pg_notify('case_jobs', new.case_id::text);
  end if;
  return null;
end
$$;

create trigger jobs_notify
  after insert or update of status, step, pct, eta_seconds, step_done, step_total, error_code
  on app.jobs
  for each row execute function app.jobs_notify();

reset role;
