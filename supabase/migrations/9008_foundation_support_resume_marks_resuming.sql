-- 9008 foundation: support's resume marks the job `resuming` (audit 15, BG-06).
--
-- A queue row may start a `paused` job only when its code is one the worker may resume (`service_busy`,
-- `model_unavailable`, `resuming`, `busy_processing`). Support's resume left the original code (`cost_guard`), so the
-- worker would now ignore the row it queued. It sets `resuming`, as the person-driven resume
-- (`app.resume_paused_job`) already does; the original code is kept in `job_support_actions.error_code`. The body is
-- 9003's `support_job_action` with that one `update`. Same signature, security definer, search path and grants.

set role app_migrator;

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
  if p_action = 'resume' then
    -- The worker starts a paused job from a queue row only for a code it may resume (BG-06); support's go-ahead is
    -- one. The reason it paused for stays in `job_support_actions.error_code` just above.
    update app.jobs set error_code = 'resuming', updated_at = now() where id = p_job;
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
