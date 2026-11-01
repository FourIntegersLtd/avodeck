-- Undo 0045: restore ai_budget to its 0006 body (page count via pages.case_id directly).
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
         (select count(*) from app.pages p where p.case_id = p_case_id);
end
$$;

reset role;
