-- 0804 close: the certificate of deletion also goes to the case's current instructing parties.
-- Recipients are role codes (never addresses on the certificate): `named_expert`, then
-- `instructing_party_1`, `instructing_party_2` ... in the order the parties were added; only parties with
-- an email and no end date. The email itself stays patient-free (names and firms are not in it).
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
    where c.id = p_case_id
    union all
    select ('instructing_party_' || n.rn)::text, n.email
    from (
      select ip.email, row_number() over (order by ip.effective_from, ip.created_at, ip.id) as rn
      from app.case_instructing_parties ip
      where ip.case_id = p_case_id and ip.effective_to is null and ip.email is not null
    ) n;
end
$$;

reset role;
