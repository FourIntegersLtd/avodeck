-- 0030 foundation: what slice O (organisations and accounts) builds on (rule 9, S15, S17, F38).
--
-- support_staff                     Avodeck support staff. Added and removed by hand in SQL by an
--                                   operator (docs/ops/support-staff.md); no API route writes it and
--                                   the runtime roles may only read their own row.
-- support_access_grants             A named expert's time-limited consent (at most 24 hours, one case)
--                                   for support to look at the case. A shared core table created here
--                                   so the access check is testable now; slice O builds the routes
--                                   (POST/DELETE /cases/{id}/support-access). Only revoked_at changes
--                                   after a grant is given. Kept after the case is deleted, like the
--                                   audit trail (ids and a short code only): the record of consent.
--                                   (A table in the foundation range cannot join the purge order on a
--                                   fresh database: 0112-0799 rewrite that list wholesale.)
-- app.is_support_staff(person)      current staff (not removed).
-- app.case_role_allowed(...)        the matrix (app.case_action_allowed) plus the case role `support`,
--                                   which may only read: case.view_metadata, case.view_content,
--                                   case.view_audit. A separate function because the matrix itself is
--                                   redefined by migrations numbered after this one (0200).
-- app.case_access(case, action)     redefined, keeping 0018's behaviour, and:
--                                   * `support` when the person has no case role, is current staff and
--                                     the case has a grant from its named expert that is not revoked
--                                     and not expired (checked on every read), for them or for any
--                                     staff member;
--                                   * `closed` (people.closed_at set, S15): only the named expert of a
--                                     case ever served keeps a role, no organisation role, and only
--                                     for the read actions; anything else gives no row (404).
-- app.can_access_case(case, action) answers through app.case_role_allowed, so row-level security
--                                   repeats both rules.
-- app.organisation_policy(org)      organisations.policy for a current member, for anyone who can see
--                                   one of the organisation's cases, or for that case's own job.

set role app_migrator;

-- Staff -------------------------------------------------------------------------------------------
create table app.support_staff (
  person_id uuid primary key references app.people (id),
  added_at timestamptz not null default now(),
  removed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint support_staff_removed_after_added check (removed_at is null or removed_at >= added_at)
);

alter table app.support_staff enable row level security;
revoke all on app.support_staff from app_api, app_worker;
grant select on app.support_staff to app_api;
create policy support_staff_self on app.support_staff for select to app_api
  using (person_id = app.current_person_id());

-- Grants ------------------------------------------------------------------------------------------
create table app.support_access_grants (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  granted_by uuid not null references app.people (id),
  granted_to uuid references app.people (id),              -- null: any current staff member
  reason text not null constraint support_access_grants_reason_code check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
  granted_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '24 hours',
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  constraint support_access_grants_at_most_a_day
    check (expires_at > granted_at and expires_at <= granted_at + interval '24 hours')
);
create index support_access_grants_case on app.support_access_grants (case_id) where revoked_at is null;

-- The running app gives a grant now, never back-dated or already revoked; a revocation is never undone.
create function app.support_access_grants_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if current_user = 'app_api' then
      new.granted_at := now();
      new.revoked_at := null;
    end if;
  elsif old.revoked_at is not null then
    perform app.refuse('this grant has already been revoked');
  end if;
  return new;
end
$$;

create trigger support_access_grants_guard before insert or update on app.support_access_grants
  for each row execute function app.support_access_grants_guard();

alter table app.support_access_grants enable row level security;
revoke all on app.support_access_grants from app_api, app_worker;
grant select, insert on app.support_access_grants to app_api;
grant update (revoked_at) on app.support_access_grants to app_api;
grant select on app.support_access_grants to app_worker;

create policy support_access_grants_view on app.support_access_grants for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy support_access_grants_give on app.support_access_grants for insert to app_api
  with check (app.can_access_case(case_id, 'support_access.grant') and granted_by = app.current_person_id());
create policy support_access_grants_revoke on app.support_access_grants for update to app_api
  using (app.can_access_case(case_id, 'support_access.grant'))
  with check (app.can_access_case(case_id, 'support_access.grant'));
create policy support_access_grants_job on app.support_access_grants for select to app_worker
  using (case_id = app.current_job_case_id());

-- Access ------------------------------------------------------------------------------------------
create function app.is_support_staff(p_person uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.support_staff s where s.person_id = p_person and s.removed_at is null)
$$;

create function app.case_role_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(case_role = 'support'
                  and action = any (array['case.view_metadata', 'case.view_content', 'case.view_audit']), false)
      or app.case_action_allowed(action, nullif(case_role, 'support'), org_role, org_kind)
$$;

drop function app.case_access(uuid, text);

create function app.case_access(p_case_id uuid, p_action text)
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

create or replace function app.can_access_case(p_case_id uuid, p_action text) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select coalesce(
    (select app.case_role_allowed(p_action, a.case_role, a.org_role, a.org_kind)
     from app.case_access(p_case_id, p_action) a),
    false)
$$;

-- Organisation policy (core/contracts/org_policy.py) ----------------------------------------------
create function app.organisation_policy(p_org uuid) returns jsonb
  language sql stable
  security definer
  set search_path = ''
as $$
  select o.policy
  from app.organisations o
  where o.id = p_org
    and (
      exists (select 1 from app.memberships m join app.people p on p.id = m.person_id
              where m.organisation_id = o.id and m.person_id = app.current_person_id() and m.ended_at is null
                and p.closed_at is null)
      or exists (select 1 from app.cases c
                 where c.organisation_id = o.id and c.status <> 'deleted'
                   and (c.named_expert_id = app.current_person_id()
                        or exists (select 1 from app.case_members cm where cm.case_id = c.id
                                   and cm.person_id = app.current_person_id() and cm.removed_at is null))
                   and app.can_access_case(c.id, 'case.view_metadata'))
      or (session_user = 'app_worker'
          and exists (select 1 from app.cases c where c.id = app.current_job_case_id() and c.organisation_id = o.id))
    )
$$;

revoke execute on function app.support_access_grants_guard() from public, app_api, app_worker;
revoke execute on function app.is_support_staff(uuid), app.organisation_policy(uuid) from public;
grant execute on function app.is_support_staff(uuid), app.organisation_policy(uuid) to app_api, app_worker;

reset role;
