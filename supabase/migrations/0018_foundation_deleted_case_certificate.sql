-- 0018 foundation: a deleted case answers exactly one action, `case.view_certificate` (rule 9).
--
-- After app.purge_case() a case is a tombstone (status 'deleted'). It stays hidden from every
-- lookup and every other action, so it looks like a case that never existed (404), but the named
-- expert and organisation owners/admins can still read its certificate of deletion (slice H).
--
--   app.case_access(case_id)          unchanged: no row for a deleted case (lists, Today).
--   app.case_access(case_id, action)  the row the access check uses for one action. For a deleted
--                                     case, a row only when the action is case.view_certificate
--                                     and the person may take it; `deleted` says which it was.
--   app.can_access_case(case, action) answers from the two-argument form.

set role app_migrator;

create function app.case_access(p_case_id uuid, p_action text)
  returns table (case_role text, org_role text, org_kind text, deleted boolean)
  language sql stable
  security definer
  set search_path = ''
as $$
  select a.case_role, a.org_role, a.org_kind, a.deleted
  from (
    select
      case when c.named_expert_id = me.id then 'named_expert'
           else (select cm.case_role from app.case_members cm
                 where cm.case_id = c.id and cm.person_id = me.id and cm.removed_at is null) end as case_role,
      (select m.role from app.memberships m
       where m.organisation_id = c.organisation_id and m.person_id = me.id and m.ended_at is null) as org_role,
      o.kind as org_kind,
      c.status = 'deleted' as deleted
    from (select app.current_person_id() as id) me
    join app.cases c on c.id = p_case_id
    join app.organisations o on o.id = c.organisation_id
    where me.id is not null
  ) a
  where not a.deleted
     or (p_action = 'case.view_certificate'
         and app.case_action_allowed(p_action, a.case_role, a.org_role, a.org_kind))
$$;

create or replace function app.can_access_case(p_case_id uuid, p_action text) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select coalesce(
    (select app.case_action_allowed(p_action, a.case_role, a.org_role, a.org_kind)
     from app.case_access(p_case_id, p_action) a),
    false)
$$;

reset role;
