-- 0026 foundation: the named expert's report inputs for one case (request from slice F, `build_report`).
-- Slice C keeps the expert's template, declarations and signature private to the expert (row-level
-- security on `report_templates`, `declarations`, `signatures`) and `conflict_declarations` /
-- `conflict_answers` to the API. Slice F builds the report in a job and on the expert's behalf, so these
-- security-definer functions open exactly the named expert's rows for one case, to:
-- * the case's own job (`app.job_case_id`), for every function;
-- * an API caller who may build the report (`report.build`) for the template, declarations and conflicts;
--   the signature only to whoever may sign (`report.sign`, the named expert);
-- * nobody else: no rows. Table policies are unchanged.
-- The bodies are PL/pgSQL so this file can name slice C's tables (0300+) before they exist.
-- * `app.case_report_template(case)`: the named expert's latest person template.
-- * `app.case_declarations(case)`: the named expert's declaration rows for the case's jurisdiction.
-- * `app.case_signature(case)`: the named expert's newest signature.
-- * `app.case_conflict_declaration(case)`: the latest declaration, and whether an answer changed after it.
-- * `app.case_conflict_answers(case)`: the current answers; the detail stays sealed under the case key.

set role app_migrator;

create function app.case_report_input_allowed(p_case uuid, p_action text) returns boolean
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select coalesce(app.current_job_case_id() = p_case, false)
         or coalesce(app.can_access_case(p_case, p_action), false)
$$;

create function app.case_report_template(p_case uuid)
  returns table (id uuid, version integer, filename text, storage_path text, sha256 text,
                 heading_map jsonb, style_map jsonb, mapping jsonb)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select t.id, t.version, t.filename, t.storage_path, t.sha256, t.heading_map, t.style_map, t.mapping
    from app.cases c
    join app.report_templates t on t.owner_type = 'person' and t.owner_id = c.named_expert_id
    where c.id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by t.version desc
    limit 1;
end
$$;

create function app.case_declarations(p_case uuid)
  returns table (jurisdiction text, kind text, text text, locked_at timestamptz)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select d.jurisdiction, d.kind, d.text, d.locked_at
    from app.cases c
    join app.declarations d on d.expert_id = c.named_expert_id and d.jurisdiction = c.jurisdiction
    where c.id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by d.kind;
end
$$;

create function app.case_signature(p_case uuid)
  returns table (id uuid, method text, storage_path text, sha256 text, width integer, height integer,
                 created_at timestamptz)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select s.id, s.method, s.storage_path, s.sha256, s.width, s.height, s.created_at
    from app.cases c
    join app.signatures s on s.expert_id = c.named_expert_id
    where c.id = p_case and app.case_report_input_allowed(p_case, 'report.sign')
    order by s.created_at desc, s.id
    limit 1;
end
$$;

create function app.case_conflict_declaration(p_case uuid)
  returns table (outcome text, declared_at timestamptz, out_of_date boolean)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select d.outcome, d.created_at,
           exists (select 1 from app.conflict_answer_history h
                   where h.case_id = d.case_id and h.recorded_at > d.created_at)
    from app.conflict_declarations d
    where d.case_id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by d.created_at desc
    limit 1;
end
$$;

create function app.case_conflict_answers(p_case uuid)
  returns table (question_key text, answer text, detail bytea)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select a.question_key, a.answer, a.detail
    from app.conflict_answers a
    where a.case_id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by a.question_key;
end
$$;

revoke execute on function app.case_report_input_allowed(uuid, text), app.case_report_template(uuid),
  app.case_declarations(uuid), app.case_signature(uuid), app.case_conflict_declaration(uuid),
  app.case_conflict_answers(uuid)
  from public, app_api, app_worker;
grant execute on function app.case_report_template(uuid), app.case_declarations(uuid), app.case_signature(uuid),
  app.case_conflict_declaration(uuid), app.case_conflict_answers(uuid)
  to app_api, app_worker;

reset role;
