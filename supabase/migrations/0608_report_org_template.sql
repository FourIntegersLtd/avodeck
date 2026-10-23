-- 0608 report: a team organisation's required template wins the report build (architecture 10.1).
-- case_report_template (0026) now returns the organisation's template when the case's organisation has
--   `policy.required_template_id` set and that template is one of the organisation's own; otherwise the named
--   expert's latest, as before. Same columns, same access check (report.build, or the case's own job).

set role app_migrator;

create or replace function app.case_report_template(p_case uuid)
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
    join app.organisations o on o.id = c.organisation_id
    join app.report_templates t
      on (o.kind = 'team' and t.owner_type = 'organisation' and t.owner_id = o.id
          and t.id::text = o.policy ->> 'required_template_id')
      or (t.owner_type = 'person' and t.owner_id = c.named_expert_id)
    where c.id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by (t.owner_type = 'organisation') desc, t.version desc
    limit 1;
end
$$;

reset role;
