-- 0142 pipeline: starting a page-batch phase and reporting its progress (ADR 0038, scalability PR 17, build step 10).
--
-- * `pipeline_start_phase(run)`: the parent has inserted a run (or moved it to `sort_waiting`) and its batches; this
--   tops the window up under the run rows' lock so the first children are queued in the same transaction. The top-up
--   itself (`pipeline_topup`) is not granted to the worker, so the parent reaches it only through here.
-- * `pipeline_report_progress(run)`: children report on the parent's row. One statement computes the step, the bar and
--   the counts from live page counts (pages in the run's batches of the phase that are not set aside), so a page
--   quarantined after the plan cannot leave the bar short of its band. `pct` only rises. The step and its counts are
--   written together and only when the step's rank is at least the current one, so two children finishing at once
--   cannot flip the label back or clear the counts. Counts and step codes only (rule 6).

set role app_migrator;

create or replace function app.pipeline_start_phase(p_run uuid)
  returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case uuid;
begin
  select case_id into v_case from app.pipeline_runs where job_id = p_run;
  if not found then
    return;
  end if;
  if app.current_job_case_id() is distinct from v_case then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  perform app.pipeline_lock_runs(v_case);
  perform app.pipeline_topup(p_run);
end
$$;
revoke execute on function app.pipeline_start_phase(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_start_phase(uuid) to app_worker;

create or replace function app.pipeline_report_progress(p_run uuid)
  returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  r app.pipeline_runs;
  v_phase text;
  v_total integer;
  v_done integer;
  v_pct integer;
  v_rank integer;
begin
  select * into r from app.pipeline_runs where job_id = p_run;
  if not found then
    return;
  end if;
  if app.current_job_case_id() is distinct from r.case_id then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  if r.state in ('ocr_waiting', 'ocr_ready') then
    v_phase := 'ocr';
  elsif r.state in ('sort_waiting', 'sort_ready') then
    v_phase := 'sort';
  else
    return;  -- the tail reports its own steps
  end if;
  select count(*) filter (
           where case when v_phase = 'ocr' then p.text_source is not null
                      else coalesce(a.extracted_at is not null, false) end),
         count(*)
    into v_done, v_total
    from app.pages p
    left join app.page_analysis a on a.page_id = p.id
   where p.bundle_id = (select c.bundle_id from app.cases c where c.id = r.case_id)
     and not p.quarantined
     and (case when v_phase = 'ocr' then p.image_path is not null
               else p.text_source is not null and p.duplicate_of_page_id is null end)
     and exists (
       select 1 from app.pipeline_batches b
        where b.run_job_id = p_run and b.phase = v_phase
          and p.bundle_page_no between b.first_page_no and b.last_page_no
     );
  v_pct := case when v_phase = 'ocr' then 30 + (10 * v_done) / greatest(v_total, 1)
                else 40 + (15 * v_done) / greatest(v_total, 1) end;
  v_rank := case v_phase when 'ocr' then 1 else 2 end;
  update app.jobs j
     set pct = greatest(coalesce(j.pct, 0), v_pct),
         step = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                when 'missing' then 4 else 0 end
                     then v_phase else j.step end,
         step_done = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                     when 'missing' then 4 else 0 end
                          then case when v_total > 0 then v_done end else j.step_done end,
         step_total = case when v_rank >= case j.step when 'ocr' then 1 when 'sort' then 2 when 'duplicates' then 3
                                                      when 'missing' then 4 else 0 end
                           then case when v_total > 0 then v_total end else j.step_total end
   where j.id = p_run;
end
$$;
revoke execute on function app.pipeline_report_progress(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_report_progress(uuid) to app_worker;

reset role;
