-- 1009 organisations: each member comes with the number of cases they are the named expert on in this
-- organisation (S27 "cases" column). A number only, never a case, and only for an owner or admin of the
-- organisation (`org.view_all_cases`); everyone else gets null. Cases deleted or being deleted are not counted.

set role app_migrator;

drop function app.organisation_members(uuid);

create function app.organisation_members(p_org uuid)
  returns table (membership_id uuid, person_id uuid, full_name text, email text, role text,
                 started_at timestamptz, ended_at timestamptz, case_count integer)
  language sql stable
  security definer
  set search_path = ''
as $$
  select m.id, m.person_id, p.full_name, p.email, m.role, m.started_at, m.ended_at,
         case when app.is_org_admin(p_org) then
           (select count(*)::integer from app.cases c
             where c.organisation_id = p_org and c.named_expert_id = m.person_id
               and c.status <> 'deleted' and c.retention_policy is distinct from 'delete_now')
         end
  from app.memberships m join app.people p on p.id = m.person_id
  where m.organisation_id = p_org
    and exists (select 1 from app.memberships mine join app.people me on me.id = mine.person_id
                where mine.organisation_id = p_org and mine.person_id = app.current_person_id()
                  and mine.ended_at is null and me.closed_at is null)
  order by m.ended_at nulls first, m.started_at, m.id
$$;

revoke execute on function app.organisation_members(uuid) from public, app_worker;
grant execute on function app.organisation_members(uuid) to app_api;

reset role;
