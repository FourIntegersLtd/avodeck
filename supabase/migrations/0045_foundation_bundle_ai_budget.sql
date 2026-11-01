-- 0045 foundation: fix app.ai_budget's page count for the bundle split.
--
-- app.pages lost its case_id column in 0044 (ADR 0034). The page count now resolves the case's
-- pages through its bundle instead.

set role app_migrator;

create or replace function app.ai_budget(p_case_id uuid)
  returns table (tokens_used bigint, pages bigint)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  if not (coalesce(app.current_job_case_id() = p_case_id, false)
          or coalesce(app.can_access_case(p_case_id, 'case.view_content'), false)) then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  return query
  select (select coalesce(sum(c.input_tokens + c.output_tokens), 0)::bigint from app.ai_calls c where c.case_id = p_case_id),
         (select count(*) from app.pages p
            join app.cases c on c.bundle_id = p.bundle_id
          where c.id = p_case_id);
end
$$;

reset role;
