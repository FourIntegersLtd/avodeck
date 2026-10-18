set role app_migrator;

create or replace function app.close_certificate_recipients(p_case_id uuid)
  returns table (role text, email text)
  language plpgsql stable
  security definer
  set search_path = ''
as $$
begin
  if app.current_job_case_id() is distinct from p_case_id then
    raise exception using errcode = '42501', message = 'only the case''s own job may read this';
  end if;
  return query
    select 'named_expert'::text, p.email
    from app.cases c join app.people p on p.id = c.named_expert_id
    where c.id = p_case_id;
end
$$;

reset role;
