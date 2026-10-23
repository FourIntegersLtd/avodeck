-- 1008 organisations: who may receive custody of a served case (architecture 10.1 "Death or incapacity";
-- legal check). Support staff cannot read `people` rows, so this answers, for current staff only:
-- app.support_case_custodian(case)  the account whose sign-in email is the records custodian the case's
--                                   named expert recorded (`people.records_custodian_email`, any case), with
--                                   whether that account is closed and whether it is support staff. No row
--                                   when no custodian is recorded, none has an account, or the caller is
--                                   not current staff. Ids and two flags only: never names or emails.

set role app_migrator;

create function app.support_case_custodian(p_case uuid)
  returns table (person_id uuid, closed boolean, staff boolean)
  language sql stable
  security definer
  set search_path = ''
as $$
  select p.id, p.closed_at is not null, app.is_support_staff(p.id)
  from app.cases c
  join app.people e on e.id = c.named_expert_id
  join app.people p on lower(p.email) = lower(e.records_custodian_email)
  where c.id = p_case and e.records_custodian_email is not null
    and app.is_support_staff(app.current_person_id())
$$;

revoke execute on function app.support_case_custodian(uuid) from public, app_worker;
grant execute on function app.support_case_custodian(uuid) to app_api;

reset role;
