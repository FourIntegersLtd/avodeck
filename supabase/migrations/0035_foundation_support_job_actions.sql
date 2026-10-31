-- 0035 foundation: Avodeck support resumes a paused job or requeues a failed one (permissions.md "Avodeck
-- staff"; state-machines.md "Job"). The routes are core (`POST /v1/support/jobs/{job_id}/resume` and
-- `/requeue`, each behind its own flag).
--
-- job_support_actions                 one row per support action on a job: which job, resume or requeue,
--                                     the reason code, and what the job was before (status, error code,
--                                     attempts), so a requeue that sets `attempts` back to 0 keeps the
--                                     history. Ids and codes only. Written only by app.support_job_action();
--                                     never changes. Goes with its job (no case_id of its own: the job's
--                                     rows go when the case is purged).
-- app.support_job_action(job, action, reason, queue)
--                                     current support staff only (else 42501). Locks the job row.
--                                     resume: a paused job of a case not on hold is handed back to the
--                                     queue now (its waiting queue row is brought forward, else a new one
--                                     is deferred with the job's lock); the worker moves it paused ->
--                                     running. requeue: a failed job goes failed -> queued with attempts 0
--                                     and is deferred. Returns an outcome code instead of raising, so
--                                     nothing is written on a refusal: ok, not_found (no such job, no case,
--                                     or a deleted case), job_not_paused, job_not_failed, case_on_hold.
--                                     Also returns the named expert's email for the patient-free notice.

set role app_migrator;

create table app.job_support_actions (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references app.jobs (id) on delete cascade,
  action text not null check (action in ('resume', 'requeue')),
  reason_code text not null check (reason_code ~ '^[a-z][a-z0-9_]{0,39}$'),
  from_status text not null,
  error_code text,
  attempts integer not null,
  person_id uuid references app.people (id) on delete set null,
  created_at timestamptz not null default now()
);
create index job_support_actions_job on app.job_support_actions (job_id);

alter table app.job_support_actions enable row level security;
revoke all on app.job_support_actions from app_api, app_worker;
grant select on app.job_support_actions to app_api;
-- Staff, and whoever may see the job's case audit trail (the jobs row's own policy decides the case).
create policy job_support_actions_view on app.job_support_actions for select to app_api
  using (app.is_support_staff(app.current_person_id())
         or exists (select 1 from app.jobs j where j.id = job_id
                    and app.can_access_case(j.case_id, 'case.view_audit')));

create function app.support_job_action(p_job uuid, p_action text, p_reason text, p_queue text)
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

revoke execute on function app.support_job_action(uuid, text, text, text) from public, app_api, app_worker;
grant execute on function app.support_job_action(uuid, text, text, text) to app_api;

reset role;
