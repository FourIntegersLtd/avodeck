-- 0064 down: the lookups by queue name again, and drop the kind lookup.

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

  -- Hand it to the queue once: a queue row still waiting (a back-off) is brought forward.
  select pj.id into v_waiting from procrastinate_jobs pj
   where pj.queue_name = p_queue and pj.args ->> 'job_id' = p_job::text and pj.status = 'todo'
   order by pj.id desc limit 1;
  if v_waiting is not null then
    update procrastinate_jobs set scheduled_at = now() where id = v_waiting;
  else
    select true, pj.lock into v_seen, v_lock from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text order by pj.id desc limit 1;
    perform procrastinate_defer_jobs_v1(array[row(
      p_queue, 'run_job', j.priority,
      case when coalesce(v_seen, false) then v_lock else 'case:' || j.case_id::text end,
      null, jsonb_build_object('job_id', p_job, 'case_id', j.case_id), null
    )::procrastinate_job_to_defer_v1]);
  end if;

  return query
    select 'ok'::text, j.case_id, (select x.status from app.jobs x where x.id = p_job),
           (select p.email from app.people p where p.id = c.named_expert_id and p.closed_at is null);
end
$$;

drop function if exists app.support_job_kind(uuid);

reset role;
