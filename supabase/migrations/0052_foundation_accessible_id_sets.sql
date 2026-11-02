-- 0052 foundation: row-level security checks access once per query, not once per row (architecture 9).
--
-- Every SELECT policy used to call app.can_access_case(case_id, '<action>') or
-- app.can_access_bundle(bundle_id, '<action>') for each row, about 10 ms each (the functions are
-- security definer, so Postgres cannot inline them). A case with 4,000 facts took 45 seconds to list.
--
-- app.accessible_case_ids(action)   the cases the signed-in person may reach for the action: exactly those
--                                   where app.can_access_case(case, action) is true. A policy now reads
--                                   `case_id in (select app.accessible_case_ids('<action>'))`, which Postgres
--                                   evaluates once per query.
-- app.accessible_bundle_ids(action) the bundles reached through such a case, as app.can_access_bundle.
-- app.use_accessible_id_sets()      rewrites every SELECT policy for app_api whose whole condition is one of
--                                   the three slow forms. It runs here, so it changes the policies that exist
--                                   when this migration is applied; policies a later migration creates in the
--                                   slow form are caught by the guard in test_foundation_rls_set_access.py and
--                                   fixed by calling this function again (or by writing the new form).
--
-- The candidate filter only narrows the work; the rules of app.case_access (0032) are then applied to each
-- candidate in one pass. If app.case_access changes, change this function too (the test compares them). A candidate is a case where the person is the named expert, has a
-- case_members row (removed or not), has a memberships row in the case's organisation (ended or not), a
-- support grant exists, or a custody transfer names them or an organisation they belong to. These are the
-- only ways app.case_access (0032) can give a person a role on a case. api/tests/repository/
-- test_foundation_rls_set_access.py proves the sets equal the per-row functions for every kind of person.

set role app_migrator;

create function app.accessible_case_ids(p_action text) returns setof uuid
  language sql stable
  security definer
  set search_path = ''
as $$
  -- The same rules as app.case_access (0032) followed by app.case_role_allowed, run for every candidate
  -- case in one pass instead of one function call per case.
  with me as (select app.current_person_id() as id),
  cand as (
    select c.id from app.cases c, me where c.named_expert_id = me.id
    union
    select cm.case_id from app.case_members cm, me where cm.person_id = me.id
    union
    select c.id from app.cases c join app.memberships m on m.organisation_id = c.organisation_id, me
      where m.person_id = me.id
    union
    select g.case_id from app.support_access_grants g, me where app.is_support_staff(me.id)
    union
    select t.case_id from app.custody_transfers t, me
      where t.to_person_id = me.id
         or t.to_organisation_id in (select m.organisation_id from app.memberships m where m.person_id = me.id)
  )
  select a.case_id
  from (
    select
      b.case_id,
      case when b.closed then (case when b.member_role = 'named_expert' and app.case_ever_served(b.case_id)
                                    then 'named_expert' end)
           when b.member_role is not null then b.member_role
           when b.support then 'support'
           when b.custodian then 'custodian' end as case_role,
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
                        and g.expires_at > now() and (g.granted_to is null or g.granted_to = me.id)) as support,
        (c.status = 'deleted' or app.case_ever_served(c.id))
          and exists (select 1 from app.custody_transfers t
                      where t.case_id = c.id and t.revoked_at is null
                        and (t.to_person_id = me.id
                             or exists (select 1 from app.memberships m
                                        where m.organisation_id = t.to_organisation_id and m.person_id = me.id
                                          and m.ended_at is null and m.role in ('owner', 'admin')))) as custodian
      from me
      join app.cases c on c.id in (select id from cand)
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
    and coalesce(app.case_role_allowed(p_action, a.case_role, a.org_role, a.org_kind), false)
$$;

create function app.accessible_bundle_ids(p_action text) returns setof uuid
  language sql stable
  security definer
  set search_path = ''
as $$
  select c.bundle_id
  from app.cases c
  where c.status <> 'deleted'
    and c.bundle_id is not null
    and c.id in (select app.accessible_case_ids(p_action))
$$;

create function app.use_accessible_id_sets() returns integer
  language plpgsql
  set search_path = ''
as $$
declare
  pol record;
  hit text[];
  new_qual text;
  changed integer := 0;
begin
  for pol in
    select schemaname, tablename, policyname, qual
    from pg_policies
    where schemaname = 'app' and cmd = 'SELECT' and 'app_api' = any (roles)
  loop
    hit := regexp_match(pol.qual, '^app\.can_access_case\(case_id, ''([a-z_.]+)''::text\)$');
    if hit is not null then
      new_qual := format('case_id in (select app.accessible_case_ids(%L))', hit[1]);
    else
      hit := regexp_match(pol.qual, '^app\.can_access_case\(id, ''([a-z_.]+)''::text\)$');
      if hit is not null then
        new_qual := format('id in (select app.accessible_case_ids(%L))', hit[1]);
      else
        hit := regexp_match(pol.qual, '^app\.can_access_bundle\(bundle_id, ''([a-z_.]+)''::text\)$');
        if hit is null then
          continue;
        end if;
        new_qual := format('bundle_id in (select app.accessible_bundle_ids(%L))', hit[1]);
      end if;
    end if;
    execute format('alter policy %I on %I.%I using (%s)', pol.policyname, pol.schemaname, pol.tablename, new_qual);
    changed := changed + 1;
  end loop;
  return changed;
end
$$;

revoke execute on function app.accessible_case_ids(text), app.accessible_bundle_ids(text),
  app.use_accessible_id_sets() from public;
grant execute on function app.accessible_case_ids(text), app.accessible_bundle_ids(text) to app_api, app_worker;

select app.use_accessible_id_sets();

reset role;
