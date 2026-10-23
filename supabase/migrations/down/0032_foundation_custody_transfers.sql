-- Undo 0032: app.case_role_allowed() and app.case_access() as in 0030; drop the custody table.
set role app_migrator;

create or replace function app.case_role_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(case_role = 'support'
                  and action = any (array['case.view_metadata', 'case.view_content', 'case.view_audit']), false)
      or app.case_action_allowed(action, nullif(case_role, 'support'), org_role, org_kind)
$$;

create or replace function app.case_access(p_case_id uuid, p_action text)
  returns table (case_role text, org_role text, org_kind text, deleted boolean, closed boolean)
  language sql stable
  security definer
  set search_path = ''
as $$
  select a.case_role, a.org_role, a.org_kind, a.deleted, a.closed
  from (
    select
      case when b.closed then (case when b.member_role = 'named_expert' and app.case_ever_served(b.case_id)
                                    then 'named_expert' end)
           when b.member_role is not null then b.member_role
           when b.support then 'support' end as case_role,
      case when b.closed then null else b.org_role end as org_role,
      b.org_kind, b.deleted, b.closed
    from (
      select
        c.id as case_id,
        case when c.named_expert_id = me.id then 'named_expert'
             else (select cm.case_role from app.case_members cm
                   where cm.case_id = c.id and cm.person_id = me.id and cm.removed_at is null) end as member_role,
        (select m.role from app.memberships m
         where m.organisation_id = c.organisation_id and m.person_id = me.id and m.ended_at is null) as org_role,
        o.kind as org_kind,
        c.status = 'deleted' as deleted,
        exists (select 1 from app.people p where p.id = me.id and p.closed_at is not null) as closed,
        app.is_support_staff(me.id)
          and exists (select 1 from app.support_access_grants g
                      where g.case_id = c.id and g.granted_by = c.named_expert_id and g.revoked_at is null
                        and g.expires_at > now() and (g.granted_to is null or g.granted_to = me.id)) as support
      from (select app.current_person_id() as id) me
      join app.cases c on c.id = p_case_id
      join app.organisations o on o.id = c.organisation_id
      where me.id is not null
    ) b
  ) a
  where (not a.closed
         or (a.case_role is not null
             and p_action = any (array['case.view_metadata', 'case.view_content', 'case.view_audit',
                                       'case.view_certificate'])))
    and (not a.deleted
         or (p_action = 'case.view_certificate'
             and app.case_role_allowed(p_action, a.case_role, a.org_role, a.org_kind)))
$$;

drop table app.custody_transfers;
drop function app.custody_transfers_guard();

reset role;
