-- 1021 organisations: fix support_case_diagnostics for the bundle split.
--
-- app.pages lost its case_id column in 0044 (ADR 0034). The page-count subquery now resolves
-- the case's pages through its bundle instead.

set role app_migrator;

create or replace function app.support_case_diagnostics(p_case uuid) returns jsonb
  language plpgsql stable
  security definer
  set search_path = ''
as $$
declare
  v_case record;
  v_report_id uuid;
  v_report_status text;
  v_report_locked timestamptz;
  v_report_served timestamptz;
  v_check_passed boolean;
  v_check_results jsonb;
  v_export_id uuid;
  v_export_status text;
  v_hold_found boolean;
  v_hold_until date;
  v_hold_reason text;
  v_out jsonb;
begin
  if not app.is_support_staff(app.current_person_id()) then
    return null;
  end if;
  select c.id, c.status, c.named_expert_id, c.delete_after, c.jurisdiction, c.case_type, c.specialty
    into v_case from app.cases c where c.id = p_case;
  if not found then
    return null;
  end if;

  select rv.id, rv.status, rv.locked_at,
         (select min(s.created_at) from app.report_servings s where s.report_version_id = rv.id)
    into v_report_id, v_report_status, v_report_locked, v_report_served
    from app.report_versions rv
   where rv.case_id = p_case and rv.type not in ('answers', 'supplementary')
   order by rv.version_no desc, rv.created_at desc limit 1;

  select r.passed, r.results into v_check_passed, v_check_results
    from app.final_check_runs r
   where r.case_id = p_case and r.report_version_id = v_report_id
   order by r.at desc, r.created_at desc limit 1;

  select j.id, j.status into v_export_id, v_export_status
    from app.jobs j where j.case_id = p_case and j.kind = 'export_case'
   order by j.created_at desc, j.updated_at desc limit 1;

  select h.until, h.reason into v_hold_until, v_hold_reason
    from app.legal_holds h where h.case_id = p_case and h.released_at is null
   order by h.created_at desc limit 1;
  v_hold_found := found;

  v_out := jsonb_build_object(
    'jobs', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', j.id, 'kind', app.support_code(j.kind), 'status', j.status, 'attempts', j.attempts,
               'error_code', app.support_code(j.error_code), 'step', app.support_code(j.step),
               'created_at', j.created_at, 'started_at', j.started_at, 'finished_at', j.finished_at,
               'updated_at', j.updated_at) order by j.created_at desc, j.id)
        from (select * from app.jobs where case_id = p_case order by created_at desc, id limit 50) j), '[]'::jsonb),
    'pages', (
      select jsonb_build_object(
               'total', count(*),
               'unread_by_named_expert', count(*) filter (
                 where p.superseded_by_page_id is null and not p.quarantined
                   and not exists (select 1 from app.page_reads r
                                    where r.page_id = p.id and r.person_id = v_case.named_expert_id)),
               'unreadable', count(*) filter (where p.unreadable),
               'needs_clearer_copy', count(*) filter (where p.needs_clearer_copy),
               'quarantined', count(*) filter (where p.quarantined),
               'superseded', count(*) filter (where p.superseded_by_page_id is not null))
        from app.pages p join app.cases c2 on c2.bundle_id = p.bundle_id where c2.id = p_case),
    'facts', jsonb_build_object(
      'suggested', (select count(*) from app.facts f where f.case_id = p_case and f.status = 'suggested'),
      'verified', (select count(*) from app.facts f where f.case_id = p_case and f.status in ('verified', 'edited')),
      'needs_recheck', (select count(*) from app.fact_flags ff
                         where ff.case_id = p_case and ff.flagged_for = 'recheck')),
    'questions', jsonb_build_object(
      'state', (select case when count(*) = 0 then 'none'
                            when count(*) filter (where q.status = 'suggested') > 0 then 'to_check'
                            else 'confirmed' end
                  from app.questions q where q.case_id = p_case),
      'instruction_versions', coalesce((
        select jsonb_agg(jsonb_build_object(
                 'n', v.version_no,
                 'read_status', case when v.kind = 'oral' or v.document_id is null then 'not_needed'
                                     when e.instruction_version_id is not null then 'read'
                                     when lj.status = 'failed' then 'failed'
                                     else 'reading' end,
                 'job_error_code', app.support_code(coalesce(ej.error_code,
                                     case when e.instruction_version_id is null then lj.error_code end)))
                 order by v.version_no)
          from app.instruction_versions v
          left join app.instruction_extractions e on e.instruction_version_id = v.id
          left join app.jobs ej on ej.id = e.job_id
          left join lateral (select j.status, j.error_code from app.jobs j
                              where j.case_id = p_case and j.kind = 'extract_questions'
                              order by j.created_at desc, j.updated_at desc limit 1) lj on true
         where v.case_id = p_case), '[]'::jsonb)),
    'report', jsonb_build_object(
      'latest_version_status', v_report_status,
      'locked_at', v_report_locked,
      'served_at', v_report_served,
      'final_check', jsonb_build_object(
        'status', case when v_check_passed is null then 'not_run' when v_check_passed then 'passed' else 'failed' end,
        'item_codes', coalesce((
          select jsonb_agg(app.support_code(item ->> 'key') order by n)
            from jsonb_array_elements(coalesce(v_check_results, '[]'::jsonb)) with ordinality as x(item, n)
           where coalesce(item ->> 'outcome', '') <> 'passed' and app.support_code(item ->> 'key') is not null),
          '[]'::jsonb)),
      'acknowledged_declaration_version', (
        select s.acknowledged_declaration_version from app.report_settings s
         where s.case_id = p_case and s.acknowledged_declaration_version ~ '^[A-Za-z0-9_.-]{1,64}$'),
      'declaration_acknowledged', exists (
        select 1 from app.report_settings s where s.case_id = p_case and s.acknowledged_declaration_version is not null),
      'signature_present', exists (select 1 from app.signatures g where g.expert_id = v_case.named_expert_id),
      'signature_readable', exists (
        select 1 from app.signatures g where g.expert_id = v_case.named_expert_id
           and g.storage_path is not null and g.sha256 is not null
           and coalesce(g.width, 1) > 0 and coalesce(g.height, 1) > 0)),
    'close', jsonb_build_object(
      'status', v_case.status,
      'delete_after', v_case.delete_after,
      'hold', jsonb_build_object('present', v_hold_found,
                                 'until', v_hold_until, 'reason_code', app.support_code(v_hold_reason)),
      -- as app.close_deletion_started() (0802), which asks for case access support staff do not have
      'deletion_started', v_case.status = 'deleted'
                          or exists (select 1 from app.deletion_certificates dc where dc.case_id = p_case),
      'export', jsonb_build_object(
        'status', coalesce(v_export_status, 'none'),
        'downloaded', v_export_id is not null and exists (
          select 1 from app.audit_log a where a.case_id = p_case and a.action = 'case.export_downloaded'
             and a.target = 'export:' || v_export_id::text))),
    'offline_packages', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', o.id, 'state', o.status, 'expires_at', o.expires_at,
               'stale_reason', case when o.stale then app.support_code(coalesce(o.stale_reason, 'stale')) end,
               'downloaded_at', o.downloaded_at) order by o.created_at desc, o.id)
        from app.offline_packages o where o.case_id = p_case), '[]'::jsonb),
    'named_expert_history', coalesce((
      select jsonb_agg(jsonb_build_object('changed_at', a.at) order by a.at, a.seq)
        from app.audit_log a where a.case_id = p_case and a.action = 'case.named_expert_changed'), '[]'::jsonb),
    'deadlines', (
      select jsonb_build_object(
               'open', count(*) filter (where d.done_at is null and d.cancelled_at is null and d.due_on >= current_date),
               'overdue', count(*) filter (where d.done_at is null and d.cancelled_at is null and d.due_on < current_date),
               'done', count(*) filter (where d.done_at is not null and d.cancelled_at is null),
               'cancelled', count(*) filter (where d.cancelled_at is not null))
        from app.deadlines d where d.case_id = p_case),
    'packs', jsonb_build_object('jurisdiction', v_case.jurisdiction, 'case_type', v_case.case_type,
                                'specialty', v_case.specialty)
  );
  return v_out;
end
$$;

reset role;
