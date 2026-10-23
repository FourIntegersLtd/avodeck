-- 1005 organisations: closing a team organisation (architecture 10.1 "Organisation closure").
--
-- app.close_organisation(org)  for a current owner whose account is open (anyone else: 42501): refuses
--                              (AV409 `personal_organisation`) a personal organisation, which closes only
--                              with the account, and (AV409 `cases_open`) while any of the organisation's
--                              cases is draft, processing or active and has never been served. Otherwise
--                              sets `closed_at`, cancels pending invites, ends every current membership
--                              (owners too) and records `organisation.closed`. Cases are not touched:
--                              served ones stay until their retention ends and their named experts keep
--                              access (access by `named_expert_id`, not by membership).
-- app.memberships_guard()      redefined from 0002 with one change: in a closed organisation the last
--                              owner's membership may end (closure ends them all).

set role app_migrator;

create or replace function app.memberships_guard() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  org_kind text;
  org_closed boolean;
begin
  if old.ended_at is not null then
    if new.ended_at is distinct from old.ended_at or new.role <> old.role then
      perform app.refuse('an ended membership cannot change');
    end if;
    return new;
  end if;
  if new.organisation_id <> old.organisation_id or new.person_id <> old.person_id then
    perform app.refuse('a membership cannot move');
  end if;
  -- Serialise changes per organisation so two owners cannot leave at once.
  select kind, closed_at is not null into org_kind, org_closed
  from app.organisations where id = old.organisation_id for update;
  if new.ended_at is not null and org_kind = 'personal'
     and exists (select 1 from app.people p where p.id = old.person_id and p.closed_at is null) then
    perform app.refuse('a personal organisation membership lasts while the account is open');
  end if;
  if old.role = 'owner' and (new.ended_at is not null or new.role <> 'owner') and not org_closed
     and not exists (
       select 1 from app.memberships m
       where m.organisation_id = old.organisation_id and m.role = 'owner' and m.ended_at is null
         and m.id <> old.id) then
    perform app.refuse('an organisation always keeps at least one owner');
  end if;
  if new.ended_at is not null then
    update app.case_members cm set removed_at = new.ended_at
    from app.cases c
    where c.id = cm.case_id and c.organisation_id = old.organisation_id
      and cm.person_id = old.person_id and cm.removed_at is null;
  end if;
  return new;
end
$$;

create function app.close_organisation(p_org uuid) returns void
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  org_kind text;
begin
  if not exists (select 1 from app.memberships m join app.people p on p.id = m.person_id
                 where m.organisation_id = p_org and m.person_id = me and m.ended_at is null
                   and m.role = 'owner' and p.closed_at is null) then
    raise exception using errcode = '42501', message = 'only an owner closes an organisation';
  end if;
  select o.kind into org_kind from app.organisations o where o.id = p_org and o.closed_at is null for update;
  if org_kind is distinct from 'team' then
    perform app.refuse('personal_organisation');
  end if;
  if exists (select 1 from app.cases c
             where c.organisation_id = p_org and c.status in ('draft', 'processing', 'active')
               and not app.case_ever_served(c.id)) then
    perform app.refuse('cases_open');
  end if;
  update app.organisations set closed_at = now() where id = p_org;
  update app.invites set status = 'cancelled' where organisation_id = p_org and status = 'pending';
  update app.memberships set ended_at = now() where organisation_id = p_org and ended_at is null;
  insert into app.audit_log (person_id, organisation_id, action, target)
  values (me, p_org, 'organisation.closed', 'organisation:' || p_org::text);
end
$$;

revoke execute on function app.close_organisation(uuid) from public;
grant execute on function app.close_organisation(uuid) to app_api;

reset role;
