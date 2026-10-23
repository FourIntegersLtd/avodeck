-- 1010 organisations: a closed organisation cannot receive custody of a served case (architecture 10.1
-- "Death or incapacity"; legal check). Support staff cannot read `organisations`, so this answers, for
-- current staff only:
-- app.support_organisation_closed(org)  whether the organisation has closed (`closed_at` set). Null for
--                                        anyone who is not current staff, or for an unknown organisation.
--                                        A yes/no only: never a name or a member.

set role app_migrator;

create function app.support_organisation_closed(p_org uuid)
  returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select o.closed_at is not null
  from app.organisations o
  where o.id = p_org and app.is_support_staff(app.current_person_id())
$$;

revoke execute on function app.support_organisation_closed(uuid) from public, app_worker;
grant execute on function app.support_organisation_closed(uuid) to app_api;

reset role;
