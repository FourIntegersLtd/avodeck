-- 0137 pipeline: the SQL that runs a page-batch run (ADR 0038, scalability PR 17).
--
-- All functions are security definer and the worker role may call them; the API role and public may not.
-- Global lock order, everywhere: the case lock `case:<id>` (procrastinate) -> `pipeline_runs` row -> page rows ->
-- `pipeline_batches` rows -> `jobs` rows -> queue rows. No function takes a `cases` row lock while it holds a
-- run row. `pipeline_redefer` and `pipeline_defer_row` use `procrastinate, pg_catalog` as their search path so
-- `procrastinate_defer_jobs_v1` resolves (like 0022 and 9001); the rest use an empty search path.
-- Counts, ids and codes only: nothing here reads page text (rule 6).

set role app_migrator;

-- One new queue row for an EXISTING job. No grant to any role: only definer functions below call it.
create or replace function app.pipeline_defer_row(p_job uuid, p_queue text, p_lock text, p_priority integer)
  returns void
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_case uuid;
begin
  select case_id into v_case from app.jobs where id = p_job;
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority, p_lock,
    null, jsonb_build_object('job_id', p_job, 'case_id', v_case), null
  )::procrastinate_job_to_defer_v1]);
end
$$;
revoke execute on function app.pipeline_defer_row(uuid, text, text, integer) from public, app_api, app_worker;

-- A wake for a running parent (p_wake) or the hand-back of a paused waiter. Returns false, changing nothing, for a
-- job or case that is gone or deleted, a held case, a job in the wrong status, or a job that already has a waiting or
-- running queue row (so two calls make one row).
create or replace function app.pipeline_redefer(p_job uuid, p_wake boolean default false)
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_queue text;
  v_lock text;
  v_found boolean;
begin
  select * into j from app.jobs where id = p_job for update;
  if not found or j.case_id is null then
    return false;
  end if;
  if (p_wake and j.status <> 'running') or (not p_wake and j.status <> 'paused') then
    return false;
  end if;
  if exists (select 1 from app.cases c where c.id = j.case_id and (c.on_hold or c.status = 'deleted')) then
    return false;
  end if;
  if exists (
    select 1 from procrastinate_jobs pj where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
  ) then
    return false;
  end if;
  select pj.queue_name, pj.lock, true into v_queue, v_lock, v_found from procrastinate_jobs pj
   where pj.args ->> 'job_id' = p_job::text order by pj.id desc limit 1;
  if not coalesce(v_found, false) then
    v_queue := 'bulk';
    v_lock := 'case:' || j.case_id::text;
  end if;
  perform app.pipeline_defer_row(p_job, v_queue, v_lock, case when p_wake then 20 else j.priority end);
  if p_wake then
    update app.jobs set requeued_stall = true where id = p_job;
  end if;
  return true;
end
$$;
revoke execute on function app.pipeline_redefer(uuid, boolean) from public, app_api, app_worker;
grant execute on function app.pipeline_redefer(uuid, boolean) to app_worker;

-- True while the case's bundle work is going on, so the jobs that used the lock to wait for the bundle keep waiting:
-- an open run with a running or queued parent, a child with a waiting or running queue row, or a parent paused with
-- a code the caller names as resumable. A run left open by a Stop, a cost-guard pause or a failure is not busy.
create or replace function app.pipeline_case_busy(p_case uuid, p_resumable text[])
  returns boolean
  language sql
  stable
  security definer
  set search_path = procrastinate, pg_catalog
as $$
  select exists (
    select 1
      from app.pipeline_runs r
      join app.jobs p on p.id = r.job_id
     where r.case_id = p_case
       and r.state not in ('done', 'closed')
       and (
         p.status in ('running', 'queued')
         or (p.status = 'paused' and p.error_code = any (p_resumable))
         or exists (
           select 1
             from app.pipeline_batches b
             join procrastinate_jobs pj on pj.args ->> 'job_id' = b.child_job_id::text
            where b.run_job_id = r.job_id and pj.status in ('todo', 'doing')
         )
       )
  )
$$;
revoke execute on function app.pipeline_case_busy(uuid, text[]) from public, app_api, app_worker;
grant execute on function app.pipeline_case_busy(uuid, text[]) to app_worker;

-- Moves every run of the case that is waiting on a finished phase to its ready state and wakes the parked parent.
-- A run fires only when no batch of its phase is open, its parent is `running`, and no OLDER run of the case that is
-- still open has a running parent (F69, F59: a paused or failed parent does not hold the case). Oldest run first, so
-- once one fires the younger ones are held by it. Called inside the run row's transaction by complete and finish, and
-- by the heal sweep. Not granted to any role.
create or replace function app.pipeline_fire_next(p_case uuid)
  returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  r app.pipeline_runs;
  v_fired boolean := false;
begin
  for r in
    select * from app.pipeline_runs
     where case_id = p_case and state in ('ocr_waiting', 'sort_waiting')
     order by created_at, job_id
       for update
  loop
    continue when not exists (select 1 from app.jobs p where p.id = r.job_id and p.status = 'running');
    continue when exists (
      select 1 from app.pipeline_batches b
       where b.run_job_id = r.job_id and b.completed_at is null
         and b.phase = case when r.state = 'ocr_waiting' then 'ocr' else 'sort' end
    );
    continue when exists (
      select 1
        from app.pipeline_runs o
        join app.jobs op on op.id = o.job_id
       where o.case_id = p_case and o.job_id <> r.job_id
         and o.state not in ('done', 'closed')
         and (o.created_at, o.job_id) < (r.created_at, r.job_id)
         and op.status = 'running'
    );
    update app.pipeline_runs
       set state = replace(state, '_waiting', '_ready'), ready_at = now(), updated_at = now()
     where job_id = r.job_id;
    perform app.pipeline_redefer(r.job_id, true);
    v_fired := true;
  end loop;
  return v_fired;
end
$$;
revoke execute on function app.pipeline_fire_next(uuid) from public, app_api, app_worker;

-- Queues children for the current phase's batches up to the run's window: while fewer children are queued or running
-- than the window allows and a batch has no child, one new `read_page_batch` job (priority 0, no case lock, the run's
-- child queue) is queued for the next batch. A batch gets a child once, ever (`child_job_id` is set once). Only for a
-- `*_waiting` run whose parent is `running`. The caller holds the run row. Not granted to any role.
create or replace function app.pipeline_has_row(p_job uuid)
  returns boolean
  language sql
  stable
  security definer
  set search_path = procrastinate, pg_catalog
as $$
  select exists (
    select 1 from procrastinate_jobs pj where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
  )
$$;
revoke execute on function app.pipeline_has_row(uuid) from public, app_api, app_worker;

create or replace function app.pipeline_topup(p_run uuid)
  returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  r app.pipeline_runs;
  nb app.pipeline_batches;
  v_child uuid;
  v_phase text;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found or r.state not in ('ocr_waiting', 'sort_waiting')
     or not exists (select 1 from app.jobs p where p.id = r.job_id and p.status = 'running') then
    return;
  end if;
  v_phase := split_part(r.state, '_', 1);
  loop
    exit when (
      select count(*) from app.pipeline_batches x
       where x.run_job_id = r.job_id and x.phase = v_phase and x.child_job_id is not null and x.completed_at is null
    ) >= r."window";
    select * into nb from app.pipeline_batches x
     where x.run_job_id = r.job_id and x.phase = v_phase and x.child_job_id is null
     order by x.seq limit 1 for update;
    exit when not found;
    insert into app.jobs (case_id, kind, priority) values (r.case_id, 'read_page_batch', 0) returning id into v_child;
    update app.pipeline_batches set child_job_id = v_child where id = nb.id;
    perform app.pipeline_defer_row(v_child, r.child_queue, null, 0);
  end loop;
end
$$;
revoke execute on function app.pipeline_topup(uuid) from public, app_api, app_worker;

-- Called in a child's own last transaction. Marks the batch done, tops the window up (one child per batch, ever),
-- and fires the next run when this was the last batch of its phase. True when this call moved a run to ready.
create or replace function app.pipeline_complete_batch(p_batch uuid)
  returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  b app.pipeline_batches;
  r app.pipeline_runs;
  v_before text;
  v_after text;
begin
  select * into b from app.pipeline_batches where id = p_batch;
  if not found then
    return false;
  end if;
  if app.current_job_case_id() is distinct from b.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  select * into r from app.pipeline_runs where job_id = b.run_job_id for update;
  update app.pipeline_batches set completed_at = now() where id = p_batch and completed_at is null;
  v_before := r.state;
  perform app.pipeline_topup(r.job_id);
  perform app.pipeline_fire_next(r.case_id);
  select state into v_after from app.pipeline_runs where job_id = r.job_id;
  return v_after <> v_before and v_after like '%\_ready';
end
$$;
revoke execute on function app.pipeline_complete_batch(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_complete_batch(uuid) to app_worker;

-- The parent's tail calls this when the run's last step is done: `finishing` -> `done`, then the next waiting run of
-- the case may fire. False (nothing changed) when the run is not finishing.
create or replace function app.pipeline_finish_run(p_run uuid)
  returns boolean
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  r app.pipeline_runs;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found then
    return false;
  end if;
  if app.current_job_case_id() is distinct from r.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  select * into r from app.pipeline_runs where job_id = p_run for update;
  if r.state <> 'finishing' then
    return false;
  end if;
  update app.pipeline_runs set state = 'done', finished_at = now(), updated_at = now() where job_id = p_run;
  perform app.pipeline_fire_next(r.case_id);
  return true;
end
$$;
revoke execute on function app.pipeline_finish_run(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_finish_run(uuid) to app_worker;

-- Failed and paused children of an unfinished batch go back on the queue, with no case lock: a failed child becomes
-- `queued` with no attempts counted and a new queue row; a paused child's sleeping row is brought forward, or gets a new
-- row when it has none. Running, queued and finished children are left alone. Then the window is topped up. Sets
-- `reopened_at`, the line the sweep's mirror ignores older child changes behind. Idempotent (a second call finds a
-- queue row on every child and adds none).
create or replace function app.pipeline_reopen_run(p_run uuid)
  returns void
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  r app.pipeline_runs;
  c record;
  v_queue text;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found then
    return;
  end if;
  if app.current_job_case_id() is distinct from r.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  select * into r from app.pipeline_runs where job_id = p_run for update;
  update app.pipeline_runs set reopened_at = now(), updated_at = now() where job_id = p_run;
  for c in
    select j.id, j.status, j.priority
      from app.pipeline_batches b
      join app.jobs j on j.id = b.child_job_id
     where b.run_job_id = p_run and b.completed_at is null and j.status in ('failed', 'paused')
     order by b.seq
       for update of j
  loop
    if c.status = 'failed' then
      update app.jobs set status = 'queued', error_code = null, attempts = 0, step = null, finished_at = null
       where id = c.id;
    end if;
    if exists (
      select 1 from procrastinate_jobs pj where pj.args ->> 'job_id' = c.id::text and pj.status in ('todo', 'doing')
    ) then
      update procrastinate_jobs pj set scheduled_at = now()
       where pj.args ->> 'job_id' = c.id::text and pj.status = 'todo' and pj.scheduled_at > now();
    else
      v_queue := null;
      select pj.queue_name into v_queue from procrastinate_jobs pj
       where pj.args ->> 'job_id' = c.id::text order by pj.id desc limit 1;
      perform app.pipeline_defer_row(c.id, coalesce(v_queue, r.child_queue), null, c.priority);
    end if;
  end loop;
  perform app.pipeline_topup(p_run);
end
$$;
revoke execute on function app.pipeline_reopen_run(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_reopen_run(uuid) to app_worker;

-- The first call of a batch-mode parent. Under the run row it puts its own `doing` queue row's priority back to the
-- job's own (a wake row carries 20 so that it is fetched; a back-off row that kept 20 would block lower priorities for
-- the case), then answers what to do: `end` (run done, closed or gone), `wake_a` (the OCR phase is read), `tail` (the
-- sort phase is read, or the tail is resuming) or `park` (a phase is still being read). Never queues a parent row.
create or replace function app.pipeline_enter(p_run uuid)
  returns text
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  r app.pipeline_runs;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found then
    return 'end';
  end if;
  if app.current_job_case_id() is distinct from r.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  select * into r from app.pipeline_runs where job_id = p_run for update;
  update procrastinate_jobs pj set priority = j.priority
    from app.jobs j
   where j.id = p_run and pj.args ->> 'job_id' = p_run::text and pj.status = 'doing' and pj.priority <> j.priority;
  if r.state in ('done', 'closed') then
    return 'end';
  end if;
  if r.state in ('ocr_waiting', 'sort_waiting') then
    perform app.pipeline_reopen_run(p_run);
    if exists (
      select 1 from app.pipeline_batches b
       where b.run_job_id = p_run and b.completed_at is null and b.phase = split_part(r.state, '_', 1)
    ) then
      return 'park';
    end if;
    r.state := replace(r.state, '_waiting', '_ready');
    update app.pipeline_runs set state = r.state, ready_at = now(), updated_at = now() where job_id = p_run;
  end if;
  if r.state = 'ocr_ready' then
    return 'wake_a';
  end if;
  update app.pipeline_runs set state = 'finishing', updated_at = now() where job_id = p_run and state = 'sort_ready';
  return 'tail';
end
$$;
revoke execute on function app.pipeline_enter(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_enter(uuid) to app_worker;

-- The sweep's mirror: the parent of a parked run shows what its children did. Returns true when it changed the parent.
-- The caller holds the run row. Under the parent's job row `for update` it re-checks everything, so a resume that got
-- there first (a queue row, `resuming`) is never overwritten (the resume race), and it does nothing for a parent that
-- already has a waiting or running queue row (that handler reopens the run itself) or that has `failed` (a failure is
-- final). Only children of unfinished batches whose last change is newer than the run's `reopened_at` count. Order of
-- evidence: a failed child fails the parent with its code (a paused parent goes `paused -> running -> failed`, the guard
-- refuses `paused -> failed`); else a paused child (not `resuming`) pauses a running parent with its code; else a parent
-- of a `*_waiting` run that is paused with a resumable code, with nothing left to mirror, goes back to `running`. A flip
-- is a status change only: attempts are counted when a worker starts a job, never here.
-- Not granted to any role.
create or replace function app.pipeline_mirror_run(p_run uuid, p_resumable text[])
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  r app.pipeline_runs;
  p app.jobs;
  v_since timestamptz;
  v_has_failed boolean := false;
  v_failed_code text;
  v_has_paused boolean := false;
  v_paused_code text;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found or r.state in ('done', 'closed') then
    return false;
  end if;
  select * into p from app.jobs where id = p_run for update;
  if not found or p.status not in ('running', 'paused') or app.pipeline_has_row(p_run) then
    return false;
  end if;
  v_since := coalesce(r.reopened_at, '-infinity'::timestamptz);
  select true, j.error_code into v_has_failed, v_failed_code
    from app.pipeline_batches b
    join app.jobs j on j.id = b.child_job_id
   where b.run_job_id = p_run and b.completed_at is null and j.status = 'failed' and j.updated_at > v_since
   order by b.seq limit 1;
  if v_has_failed then
    if p.status = 'paused' then
      update app.jobs set status = 'running', error_code = null where id = p_run;
    end if;
    update app.jobs set status = 'failed', error_code = coalesce(v_failed_code, 'job_error'), finished_at = now()
     where id = p_run;
    return true;
  end if;
  select true, j.error_code into v_has_paused, v_paused_code
    from app.pipeline_batches b
    join app.jobs j on j.id = b.child_job_id
   where b.run_job_id = p_run and b.completed_at is null and j.status = 'paused'
     and j.error_code is distinct from 'resuming' and j.updated_at > v_since
   order by b.seq limit 1;
  if v_has_paused then
    if p.status = 'running' then
      update app.jobs set status = 'paused', error_code = v_paused_code where id = p_run;
      return true;
    end if;
    return false;
  end if;
  if p.status = 'paused' and p.error_code = any (p_resumable) and r.state in ('ocr_waiting', 'sort_waiting') then
    update app.jobs set status = 'running', error_code = null where id = p_run;
    return true;
  end if;
  return false;
end
$$;
revoke execute on function app.pipeline_mirror_run(uuid, text[]) from public, app_api, app_worker;

-- A new bundle job takes over the case's open runs (`plan_ocr` calls this first). For each open run other than its
-- own, oldest first, under the run row: the mirror runs, so a pause or failure the sweep has not yet written is seen;
-- then the run is closed when its parent is `paused` or `failed`, or a stop was asked after the parent was created
-- and after `reopened_at` (a cancel and a start within one sweep round still take over). A parent still `running` is
-- moved to `paused` (`cancelled`) so it does not look live. A healthy run (parent running or queued, no stop) stays
-- open. Returns the number of runs closed.
create or replace function app.pipeline_close_runs(p_case uuid, p_by uuid, p_resumable text[])
  returns integer
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  r app.pipeline_runs;
  p app.jobs;
  v_stop timestamptz;
  v_n integer := 0;
begin
  if app.current_job_case_id() is distinct from p_case then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  select cp.cancel_requested_at into v_stop from app.case_processing cp where cp.case_id = p_case;
  for r in
    select * from app.pipeline_runs
     where case_id = p_case and job_id <> p_by and state not in ('done', 'closed')
     order by created_at, job_id
       for update
  loop
    perform app.pipeline_mirror_run(r.job_id, p_resumable);
    select * into p from app.jobs where id = r.job_id for update;
    if not found then
      continue;
    end if;
    if p.status in ('paused', 'failed')
       or (p.status in ('running', 'queued')
           and v_stop is not null
           and v_stop > greatest(p.created_at, coalesce(r.reopened_at, '-infinity'::timestamptz))) then
      if p.status = 'running' then
        update app.jobs set status = 'paused', error_code = 'cancelled' where id = r.job_id;
      end if;
      update app.pipeline_runs
         set state = 'closed', closed_at = now(), closed_by_job_id = p_by, updated_at = now()
       where job_id = r.job_id;
      v_n := v_n + 1;
    end if;
  end loop;
  return v_n;
end
$$;
revoke execute on function app.pipeline_close_runs(uuid, uuid, text[]) from public, app_api, app_worker;
grant execute on function app.pipeline_close_runs(uuid, uuid, text[]) to app_worker;

-- The housekeeping sweep, on the anonymous worker connection (no person, no `app.job_case_id`). Skips held and deleted
-- cases. For each case it first takes ALL the case's open run rows with `skip locked` and leaves the case to the next
-- round if any is held by a fan-in, so it never waits on a run row while holding another (no deadlock with a fan-in
-- that holds one run and wants the case's others). Then, per run, oldest first, the pass order is pinned:
--   mirror -> (i) re-defer paused children that lost their queue row (no lock) -> (ii) re-defer a parent paused with a
--   resumable code in its own tail (the case lock) -> wake a running parent of a `*_ready` or `finishing` run that has no
--   queue row -> top-up the window -> keep a silent parked parent alive (touch after 5 minutes);
-- then fire the case's next run. Last, waiters: a job paused `busy_processing` is handed back once, with its own
-- priority, when the case is no longer busy, re-checked under the case's run rows. Every write is guarded by its own
-- recheck (`pipeline_redefer`, `pipeline_has_row`, the jobs row `for update`), so a second sweep is a no-op and a sweep
-- racing a resume leaves one queue row. Returns the number of changes. Ids, codes and counts only.
create or replace function app.pipeline_heal_runs(p_resumable text[])
  returns integer
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_case uuid;
  v_open bigint;
  v_got bigint;
  r app.pipeline_runs;
  p app.jobs;
  c record;
  cj app.jobs;
  v_queue text;
  v_n integer := 0;
begin
  for v_case in
    select distinct pr.case_id
      from app.pipeline_runs pr
      join app.cases cs on cs.id = pr.case_id
     where pr.state not in ('done', 'closed') and not cs.on_hold and cs.status <> 'deleted'
     order by pr.case_id
  loop
    select count(*) into v_open from app.pipeline_runs where case_id = v_case and state not in ('done', 'closed');
    perform 1 from app.pipeline_runs
     where case_id = v_case and state not in ('done', 'closed')
     order by created_at, job_id
       for update skip locked;
    get diagnostics v_got = row_count;
    continue when v_got < v_open;
    for r in
      select * from app.pipeline_runs
       where case_id = v_case and state not in ('done', 'closed') order by created_at, job_id
    loop
      if app.pipeline_mirror_run(r.job_id, p_resumable) then
        v_n := v_n + 1;
      end if;
      select * into p from app.jobs where id = r.job_id;
      if p.status in ('running', 'paused') then
        for c in
          select j.id
            from app.pipeline_batches b
            join app.jobs j on j.id = b.child_job_id
           where b.run_job_id = r.job_id and b.completed_at is null and j.status = 'paused'
             and (j.error_code = any (p_resumable) or j.error_code = 'resuming')
           order by b.seq
        loop
          select * into cj from app.jobs where id = c.id for update;
          continue when cj.status <> 'paused' or app.pipeline_has_row(cj.id);
          v_queue := null;
          select pj.queue_name into v_queue from procrastinate_jobs pj
           where pj.args ->> 'job_id' = cj.id::text order by pj.id desc limit 1;
          perform app.pipeline_defer_row(cj.id, coalesce(v_queue, r.child_queue), null, cj.priority);
          v_n := v_n + 1;
        end loop;
      end if;
      if r.state in ('ocr_ready', 'sort_ready', 'finishing') then
        if p.status = 'paused' and (p.error_code = any (p_resumable) or p.error_code = 'resuming') then
          if app.pipeline_redefer(r.job_id, false) then
            v_n := v_n + 1;
          end if;
        elsif p.status = 'running' then
          if app.pipeline_redefer(r.job_id, true) then
            v_n := v_n + 1;
          end if;
        end if;
      else
        perform app.pipeline_topup(r.job_id);
        update app.jobs set updated_at = now()
         where id = r.job_id and status = 'running' and updated_at < now() - interval '5 minutes';
      end if;
    end loop;
    if app.pipeline_fire_next(v_case) then
      v_n := v_n + 1;
    end if;
  end loop;

  for v_case in
    select distinct j.case_id
      from app.jobs j
      join app.cases cs on cs.id = j.case_id
     where j.status = 'paused' and j.error_code = 'busy_processing' and not cs.on_hold and cs.status <> 'deleted'
     order by j.case_id
  loop
    select count(*) into v_open from app.pipeline_runs where case_id = v_case and state not in ('done', 'closed');
    perform 1 from app.pipeline_runs
     where case_id = v_case and state not in ('done', 'closed')
     order by created_at, job_id
       for update skip locked;
    get diagnostics v_got = row_count;
    continue when v_got < v_open or app.pipeline_case_busy(v_case, p_resumable);
    for c in
      select j.id from app.jobs j
       where j.case_id = v_case and j.status = 'paused' and j.error_code = 'busy_processing'
       order by j.created_at, j.id
    loop
      if app.pipeline_redefer(c.id, false) then
        v_n := v_n + 1;
      end if;
    end loop;
  end loop;
  return v_n;
end
$$;
revoke execute on function app.pipeline_heal_runs(text[]) from public, app_api, app_worker;
grant execute on function app.pipeline_heal_runs(text[]) to app_worker;

reset role;
