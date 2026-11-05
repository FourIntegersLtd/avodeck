-- 9003 foundation: a page-batch child is put back with no case lock (ADR 0038, F66).
--
-- A page-batch child (a `pipeline_batches` row names it as `child_job_id`) must never take the case lock `case:<id>`,
-- or one child would block the case's other work. Two foundation functions put a job back with a new queue row and
-- fall back to that lock when the job's earlier rows carry none: app.resume_paused_job() ("Try again") and
-- app.support_job_action() (support's resume and requeue). Both now give a batch child a null lock. The third,
-- app.requeue_orphaned_jobs(), is pipeline-owned and changed in 0138.
--
-- Both bodies start from the CURRENT definitions, not the first ones: resume_paused_job from 9001 and
-- support_job_action from 0064, so the fix they carry stays (the waiting row is found by the job on whatever queue it
-- waits and brought forward; p_queue names only a NEW row's queue). A lower number would be overwritten by 9001, which
-- is why this is in the last foundation range. Same signatures, security definer, search paths and grants.
--
-- The rule is inert until the pipeline creates `pipeline_batches` (0136): app.is_batch_child() answers false while the
-- table does not exist, and looks it up with dynamic SQL so these functions compile without it. Numbered 9003 (the
-- design said 9002, which 9002_foundation_jobs_stall_requeues took).

set role app_migrator;

create function app.is_batch_child(p_job uuid)
  returns boolean
  language plpgsql
  stable
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_child boolean;
begin
  if to_regclass('app.pipeline_batches') is null then
    return false;
  end if;
  execute 'select exists (select 1 from app.pipeline_batches b where b.child_job_id = $1)' into v_child using p_job;
  return coalesce(v_child, false);
end
$$;

revoke execute on function app.is_batch_child(uuid) from public, app_api, app_worker;

create or replace function app.resume_paused_job(p_job uuid, p_queue text default 'jobs')
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_waiting bigint;
  v_lock text;
begin
  if p_queue is null or p_queue not in ('jobs', 'bulk', 'interactive') then
    raise exception using errcode = '22023', message = 'unknown job queue';
  end if;
  select * into j from app.jobs where id = p_job for update;
  if not found or j.case_id is null or not coalesce(app.can_access_case(j.case_id, 'page.read'), false) then
    return false;
  end if;
  if j.status <> 'paused' then
    return false;
  end if;
  if exists (select 1 from app.cases c where c.id = j.case_id and (c.on_hold or c.status = 'deleted')) then
    return false;
  end if;
  -- Already handed back and still waiting for the worker: nothing more to do.
  if j.error_code = 'resuming' and exists (
    select 1 from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
  ) then
    return false;
  end if;

  update app.jobs set error_code = 'resuming', updated_at = now() where id = p_job;

  select pj.id into v_waiting from procrastinate_jobs pj
   where pj.args ->> 'job_id' = p_job::text and pj.status = 'todo'
   order by pj.id desc limit 1;
  if v_waiting is not null then
    update procrastinate_jobs set scheduled_at = now() where id = v_waiting;
  else
    select pj.lock into v_lock from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text and pj.lock is not null order by pj.id desc limit 1;
    perform procrastinate_defer_jobs_v1(array[row(
      p_queue, 'run_job', j.priority,
      case when app.is_batch_child(p_job) then null else coalesce(v_lock, 'case:' || j.case_id::text) end,
      null, jsonb_build_object('job_id', p_job, 'case_id', j.case_id), null
    )::procrastinate_job_to_defer_v1]);
  end if;
  return true;
end
$$;

create or replace function app.support_job_action(p_job uuid, p_action text, p_reason text, p_queue text)
  returns table (outcome text, case_id uuid, status text, expert_email text)
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  me constant uuid := app.current_person_id();
  j app.jobs;
  c app.cases;
  v_waiting bigint;
  v_lock text;
  v_seen boolean;
begin
  if me is null or not app.is_support_staff(me) then
    raise exception using errcode = '42501', message = 'support staff only';
  end if;
  if p_action not in ('resume', 'requeue') then
    raise exception using errcode = '22023', message = 'unknown support job action';
  end if;
  select * into j from app.jobs where id = p_job for update;
  if not found or j.case_id is null then
    return query select 'not_found'::text, null::uuid, null::text, null::text;
    return;
  end if;
  select * into c from app.cases where id = j.case_id;
  if not found or c.status = 'deleted' then
    return query select 'not_found'::text, null::uuid, null::text, null::text;
    return;
  end if;
  if p_action = 'resume' and j.status <> 'paused' then
    return query select 'job_not_paused'::text, j.case_id, j.status, null::text;
    return;
  end if;
  if p_action = 'requeue' and j.status <> 'failed' then
    return query select 'job_not_failed'::text, j.case_id, j.status, null::text;
    return;
  end if;
  if p_action = 'resume' and c.on_hold then
    return query select 'case_on_hold'::text, j.case_id, j.status, null::text;
    return;
  end if;

  insert into app.job_support_actions (job_id, action, reason_code, from_status, error_code, attempts, person_id)
  values (p_job, p_action, p_reason, j.status, j.error_code, j.attempts, me);
  if p_action = 'requeue' then
    update app.jobs set status = 'queued', attempts = 0, finished_at = null where id = p_job;
  end if;

  -- Hand it to the queue once: a queue row still waiting (a back-off) is brought forward.
  select pj.id into v_waiting from procrastinate_jobs pj
   where pj.args ->> 'job_id' = p_job::text and pj.status = 'todo'
   order by pj.id desc limit 1;
  if v_waiting is not null then
    update procrastinate_jobs set scheduled_at = now() where id = v_waiting;
  else
    select true, pj.lock into v_seen, v_lock from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text order by pj.id desc limit 1;
    perform procrastinate_defer_jobs_v1(array[row(
      p_queue, 'run_job', j.priority,
      case when app.is_batch_child(p_job) then null
           when coalesce(v_seen, false) then v_lock
           else 'case:' || j.case_id::text end,
      null, jsonb_build_object('job_id', p_job, 'case_id', j.case_id), null
    )::procrastinate_job_to_defer_v1]);
  end if;

  return query
    select 'ok'::text, j.case_id, (select x.status from app.jobs x where x.id = p_job),
           (select p.email from app.people p where p.id = c.named_expert_id and p.closed_at is null);
end
$$;

reset role;
