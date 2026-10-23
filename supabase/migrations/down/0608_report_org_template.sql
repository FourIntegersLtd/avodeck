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
    join app.report_templates t on t.owner_type = 'person' and t.owner_id = c.named_expert_id
    where c.id = p_case and app.case_report_input_allowed(p_case, 'report.build')
    order by t.version desc
    limit 1;
end
$$;

reset role;
