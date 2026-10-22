-- 1001 organisations: members (architecture 10.1, state-machines.md Membership, F32).
--
-- app.organisation_members(org)  every membership of the organisation (current first, then ended, with
--                                dates), with each person's name and email, for a current member whose
--                                account is open; nothing for anyone else. Security definer because
--                                `people` rows are visible only to the person themselves.
-- memberships                    the running app may change only `role` and `ended_at`:
--                                * a current owner or admin of the organisation (row-level security);
--                                * the member themselves, to leave (ending their own membership only);
-- memberships_actor (trigger)    for the running app, repeats slice O's rules with stable refusal codes,
--                                before the foundation's `memberships_guard`:
--                                * only an owner changes an owner's role or makes someone an owner; an
--                                  admin cannot end an owner's membership; nobody else changes roles or
--                                  ends someone else's membership (42501);
--                                * `ended_at` is now, set by the database;
--                                * `personal_membership` (AV409): a personal organisation membership lasts
--                                  while the account is open;
--                                * `last_owner` (AV409, F32): an open organisation keeps at least one owner.
--                                `memberships_guard` (0002) still serialises and repeats the last two.

set role app_migrator;

create function app.organisation_members(p_org uuid)
  returns table (membership_id uuid, person_id uuid, full_name text, email text, role text,
                 started_at timestamptz, ended_at timestamptz)
  language sql stable
  security definer
  set search_path = ''
as $$
  select m.id, m.person_id, p.full_name, p.email, m.role, m.started_at, m.ended_at
  from app.memberships m join app.people p on p.id = m.person_id
  where m.organisation_id = p_org
    and exists (select 1 from app.memberships mine join app.people me on me.id = mine.person_id
                where mine.organisation_id = p_org and mine.person_id = app.current_person_id()
                  and mine.ended_at is null and me.closed_at is null)
  order by m.ended_at nulls first, m.started_at, m.id
$$;

create function app.memberships_actor() returns trigger
  language plpgsql
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  my_role text;
  org_kind text;
  org_closed boolean;
begin
  if current_user <> 'app_api' then
    return new;
  end if;
  if old.ended_at is not null then
    return new;  -- memberships_guard refuses any change to an ended membership
  end if;
  select m.role into my_role
  from app.memberships m join app.people p on p.id = m.person_id
  where m.organisation_id = old.organisation_id and m.person_id = me and m.ended_at is null and p.closed_at is null;
  if new.role is distinct from old.role then
    if my_role is distinct from 'owner'
       and (my_role is distinct from 'admin' or old.role = 'owner' or new.role = 'owner') then
      raise exception using errcode = '42501', message = 'this role change needs an owner';
    end if;
  end if;
  if new.ended_at is not null then
    new.ended_at := now();
    if old.person_id is distinct from me
       and (my_role is null or my_role not in ('owner', 'admin') or (my_role = 'admin' and old.role = 'owner')) then
      raise exception using errcode = '42501', message = 'ending this membership needs an owner';
    end if;
  end if;
  select o.kind, o.closed_at is not null into org_kind, org_closed
  from app.organisations o where o.id = old.organisation_id;
  if new.ended_at is not null and org_kind = 'personal'
     and exists (select 1 from app.people p where p.id = old.person_id and p.closed_at is null) then
    perform app.refuse('personal_membership');
  end if;
  if old.role = 'owner' and (new.ended_at is not null or new.role <> 'owner') and not coalesce(org_closed, false)
     and not exists (select 1 from app.memberships m
                     where m.organisation_id = old.organisation_id and m.role = 'owner' and m.ended_at is null
                       and m.id <> old.id) then
    perform app.refuse('last_owner');
  end if;
  return new;
end
$$;

-- Named to run before memberships_guard (triggers fire in name order).
create trigger memberships_actor before update on app.memberships
  for each row execute function app.memberships_actor();

revoke update on app.memberships from app_api, app_worker;
grant update (role, ended_at) on app.memberships to app_api;
create policy memberships_admin_change on app.memberships for update to app_api
  using (app.is_org_admin(organisation_id)) with check (app.is_org_admin(organisation_id));
create policy memberships_leave on app.memberships for update to app_api
  using (person_id = app.current_person_id()) with check (person_id = app.current_person_id());

revoke execute on function app.organisation_members(uuid), app.memberships_actor() from public;
grant execute on function app.organisation_members(uuid) to app_api;
grant execute on function app.memberships_actor() to app_api;

reset role;
