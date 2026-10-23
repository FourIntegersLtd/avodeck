-- 0032 foundation: records custody after the named expert can no longer act (architecture 10.1 "Death or
-- incapacity"; ADR 0012, legal check). Slice O builds the route (`POST /v1/support/custody-transfers`,
-- behind the flag `support_custody_transfer`); the table and the access rule are core.
--
-- custody_transfers                 support staff record that a served case's records pass to a person, or
--                                   to an organisation (its current owners and admins hold them). Kept
--                                   after the case is deleted, like the audit trail: ids, dates and a short
--                                   note about the evidence seen (about the expert, never the patient). The
--                                   running app records one only as current staff, in their own name, now,
--                                   from the case's current named expert; only `revoked_at` changes, once.
--                                   A case never served is refused (AV409). The recipient is the records
--                                   custodian the expert named (`people.records_custodian_email` is their
--                                   sign-in email) or the case's own organisation; never a support staff
--                                   member or the person recording it (AV409), so nobody in support can
--                                   hand a case to themselves. Not in the purge order (a
--                                   table in the foundation range cannot join it on a fresh database).
-- app.case_role_allowed(...)        redefined from 0030: the case role `custodian` may take only
--                                   case.view_metadata, case.view_content, case.view_audit and
--                                   case.view_certificate.
-- app.case_access(case, action)     redefined from 0030, keeping all of it, and: `custodian` when the person
--                                   has no case role of their own and is not support, the case has been
--                                   served (or is the tombstone of one) and a transfer that is not revoked
--                                   names them, or names an organisation of which they are a current owner
--                                   or admin. A closed account holds no custody (the closed rule keeps only
--                                   the named expert).

set role app_migrator;

create table app.custody_transfers (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  from_person_id uuid not null references app.people (id),
  to_person_id uuid references app.people (id),
  to_organisation_id uuid references app.organisations (id),
  evidence_note text not null check (char_length(evidence_note) between 1 and 200),
  transferred_by uuid not null references app.people (id),
  transferred_at timestamptz not null default now(),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  constraint custody_transfers_one_recipient check (num_nonnulls(to_person_id, to_organisation_id) = 1),
  constraint custody_transfers_revoked_after check (revoked_at is null or revoked_at >= transferred_at)
);
create index custody_transfers_case on app.custody_transfers (case_id) where revoked_at is null;
create index custody_transfers_person on app.custody_transfers (to_person_id) where revoked_at is null;
create index custody_transfers_organisation on app.custody_transfers (to_organisation_id) where revoked_at is null;

create function app.custody_transfers_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  case_org uuid;
  expert uuid;
begin
  if tg_op = 'INSERT' then
    if not app.case_ever_served(new.case_id) then
      perform app.refuse('only the records of a served case pass to a custodian');
    end if;
    select c.organisation_id, c.named_expert_id into case_org, expert from app.cases c where c.id = new.case_id;
    if session_user = 'app_api' then  -- the running app (this function runs as its owner)
      new.transferred_at := now();
      new.revoked_at := null;
      new.from_person_id := expert;
    end if;
    if new.to_organisation_id is not null and new.to_organisation_id is distinct from case_org then
      perform app.refuse('custody passes only to the case''s own organisation');
    end if;
    if new.to_person_id is not null then
      -- Recording is for current staff only (row-level security refuses anyone else, 42501), so a recipient
      -- who records their own transfer is staff too.
      if app.is_support_staff(new.to_person_id)
         or (new.to_person_id = new.transferred_by and app.is_support_staff(new.transferred_by)) then
        perform app.refuse('support staff never receive custody');
      end if;
      if not exists (select 1 from app.people e join app.people r on r.id = new.to_person_id
                     where e.id = expert and e.records_custodian_email is not null
                       and lower(r.email) = lower(e.records_custodian_email)) then
        perform app.refuse('custody passes only to the records custodian the expert named');
      end if;
    end if;
    return new;
  end if;
  if old.revoked_at is not null then
    perform app.refuse('this custody transfer has already been revoked');
  end if;
  if (to_jsonb(new) - 'revoked_at') is distinct from (to_jsonb(old) - 'revoked_at') then
    perform app.refuse('only revoking changes a custody transfer');
  end if;
  return new;
end
$$;

create trigger custody_transfers_guard before insert or update on app.custody_transfers
  for each row execute function app.custody_transfers_guard();

alter table app.custody_transfers enable row level security;
revoke all on app.custody_transfers from app_api, app_worker;
grant select, insert on app.custody_transfers to app_api;
grant update (revoked_at) on app.custody_transfers to app_api;

create policy custody_transfers_staff on app.custody_transfers for select to app_api
  using (app.is_support_staff(app.current_person_id()));
create policy custody_transfers_recipient on app.custody_transfers for select to app_api
  using (to_person_id = app.current_person_id() or app.can_access_case(case_id, 'case.view_audit'));
create policy custody_transfers_record on app.custody_transfers for insert to app_api
  with check (app.is_support_staff(app.current_person_id()) and transferred_by = app.current_person_id());
create policy custody_transfers_revoke on app.custody_transfers for update to app_api
  using (app.is_support_staff(app.current_person_id()))
  with check (app.is_support_staff(app.current_person_id()));

-- Access ------------------------------------------------------------------------------------------
create or replace function app.case_role_allowed(action text, case_role text, org_role text, org_kind text)
  returns boolean
  language sql immutable parallel safe
  set search_path = ''
as $$
  select coalesce(case_role = 'support'
                  and action = any (array['case.view_metadata', 'case.view_content', 'case.view_audit']), false)
      or coalesce(case_role = 'custodian'
                  and action = any (array['case.view_metadata', 'case.view_content', 'case.view_audit',
                                          'case.view_certificate']), false)
      or app.case_action_allowed(action, nullif(nullif(case_role, 'support'), 'custodian'), org_role, org_kind)
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

revoke execute on function app.custody_transfers_guard() from public, app_api, app_worker;

reset role;
