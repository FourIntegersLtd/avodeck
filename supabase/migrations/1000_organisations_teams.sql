-- 1000 organisations: teams (architecture 10.1 "Organisations", 5.4 "Organisations and accounts").
--
-- app.is_org_admin(org)               the signed-in person is a current owner or admin of the organisation
--                                     and their account is open. Security definer so policies on
--                                     memberships-related tables can call it without recursing.
-- app.create_team_organisation(...)   creates a team organisation and makes the signed-in person its owner,
--                                     in one step (the running app cannot insert either row directly), and
--                                     records `organisation.created`.
-- organisations                       the running app may change only `name` and `policy`, and only as a
--                                     current owner or admin (row-level security repeats require_org_access).

set role app_migrator;

create function app.is_org_admin(p_org uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (
    select 1 from app.memberships m join app.people p on p.id = m.person_id
    where m.organisation_id = p_org and m.person_id = app.current_person_id() and m.ended_at is null
      and m.role in ('owner', 'admin') and p.closed_at is null)
$$;

create function app.create_team_organisation(p_name text, p_region text) returns uuid
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  org uuid;
  clean text := btrim(coalesce(p_name, ''));
begin
  if me is null or not exists (select 1 from app.people p where p.id = me and p.closed_at is null) then
    raise exception using errcode = '42501', message = 'nobody is signed in';
  end if;
  if length(clean) not between 1 and 200 or coalesce(p_region, '') !~ '^[a-z]{2,10}$' then
    raise exception using errcode = '22023', message = 'a team needs a name and a region';
  end if;
  insert into app.organisations (name, kind, region) values (clean, 'team', p_region) returning id into org;
  insert into app.memberships (organisation_id, person_id, role) values (org, me, 'owner');
  insert into app.audit_log (person_id, organisation_id, action, target)
  values (me, org, 'organisation.created', 'organisation:' || org::text);
  return org;
end
$$;

revoke update on app.organisations from app_api, app_worker;
grant update (name, policy) on app.organisations to app_api;
create policy organisations_admin_change on app.organisations for update to app_api
  using (app.is_org_admin(id)) with check (app.is_org_admin(id));

revoke execute on function app.is_org_admin(uuid), app.create_team_organisation(text, text) from public;
grant execute on function app.is_org_admin(uuid), app.create_team_organisation(text, text) to app_api;

reset role;
