-- 0070 foundation: `app.enqueue_job` no longer trusts its caller's priority or queue, and a case holds at most so many
-- jobs of one kind at once (audit 15, BG-09 and BG-11).
--
-- BG-09. The function took the queue name and the priority from its caller, so a role that could reach it could put a
-- job on the interactive queue at priority 1,000,000. Now the priority is 0 or 10 (the two the app uses), and a kind
-- listed in `app.job_kind_actions` with an `interactive` flag has the priority that flag gives it (10 or 0) and may
-- not be put on the other kind's queue (a name ending `bulk` or `interactive`). A kind with no flag is only held to
-- the two priorities. The Python registry (`core/jobs.py`) names the same flag; a core test checks they agree.
--
-- BG-11. `max_active` is the most queued or running jobs of that kind one case may hold (default 5). Past it the
-- function raises SQLSTATE AVCAP and the API answers 429 `too_many_jobs`. A worker inside its own job (the page-batch
-- children, the heal sweep, a step that queues the next) is not held to it. A person's own click cannot fill the queue.
--
-- The flag and cap values are set in 9006, after every slice's rows exist (a row inserted after this migration would
-- otherwise start with no flag).

set role app_migrator;

alter table app.job_kind_actions
  add column interactive boolean,
  add column max_active integer check (max_active is null or max_active >= 1);

create or replace function app.enqueue_job(
  p_case_id uuid, p_kind text, p_queue text, p_priority integer, p_changes_case boolean, p_code_version text
) returns uuid
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_id uuid;
  v_action text;
  v_interactive boolean;
  v_max integer;
  v_active integer;
begin
  select a.action, a.interactive, a.max_active into v_action, v_interactive, v_max
    from app.job_kind_actions a where a.kind = p_kind;
  v_action := coalesce(v_action, 'case.upload');
  if not (coalesce(app.current_job_case_id() = p_case_id, false)
          or coalesce(app.can_access_case(p_case_id, v_action), false)) then
    raise exception using errcode = '42501', message = 'this job needs ' || v_action;
  end if;
  if p_priority is null or p_priority not in (0, 10) then
    raise exception using errcode = '22023', message = 'a job has priority 0 or 10';
  end if;
  if v_interactive is not null
     and (p_priority <> case when v_interactive then 10 else 0 end
          or (v_interactive and p_queue like '%bulk')
          or (not v_interactive and p_queue like '%interactive')) then
    raise exception using errcode = '22023', message = 'that kind does not run with that priority and queue';
  end if;
  if not coalesce(app.current_job_case_id() = p_case_id, false) then
    -- One at a time per case and kind, so two requests cannot both see room for the last place.
    perform pg_advisory_xact_lock(hashtextextended(p_case_id::text || ':' || p_kind, 0));
    select count(*) into v_active from app.jobs
     where case_id = p_case_id and kind = p_kind and status in ('queued', 'running');
    if v_active >= coalesce(v_max, 5) then
      raise exception using errcode = 'AVCAP', message = 'too many jobs of that kind are waiting for this case';
    end if;
  end if;
  insert into app.jobs (case_id, kind, priority, code_version)
  values (p_case_id, p_kind, p_priority, p_code_version)
  returning id into v_id;
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority,
    case when p_changes_case then 'case:' || p_case_id::text end,
    null, jsonb_build_object('job_id', v_id, 'case_id', p_case_id), null
  )::procrastinate_job_to_defer_v1]);
  return v_id;
end
$$;

reset role;
