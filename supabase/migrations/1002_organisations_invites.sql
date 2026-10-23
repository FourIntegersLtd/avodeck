-- 1002 organisations: invites (architecture 4.2, 10.1 "Invites"; state-machines.md Invite).
--
-- invites                     an invitation to join an organisation, by email, with a role. Only the
--                             SHA-256 of the link's token is kept. Valid for 7 days, used once.
--                             pending -> accepted / cancelled / expired, all terminal; a pending invite
--                             whose expires_at has passed reads as expired. Not case data (no case_id).
--                             An owner or admin of the organisation sees, sends and cancels them; only an
--                             owner invites an owner. The running app sends invites now, for 7 days,
--                             pending, in its own name, with the email in lower case.
-- app.accept_invite(hash)     the signed-in person accepts: the invite must be pending and not expired,
--                             for the person's own email, to an open organisation they are not already in.
--                             Creates the membership and records `invite.accepted`. Refusals are AV409
--                             with a code: invite_not_found (also for someone else's invite),
--                             invite_expired, invite_closed, already_member.

set role app_migrator;

create table app.invites (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references app.organisations (id),
  email text not null check (email ~ '^[^@\s]+@[^@\s]+$' and length(email) <= 254),
  role text not null check (role in ('owner', 'admin', 'expert', 'assistant', 'viewer')),
  invited_by uuid not null default app.current_person_id() references app.people (id),
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  status text not null default 'pending' check (status in ('pending', 'accepted', 'cancelled', 'expired')),
  expires_at timestamptz not null default now() + interval '7 days',
  accepted_at timestamptz,
  accepted_by uuid references app.people (id),
  cancelled_at timestamptz,
  created_at timestamptz not null default now()
);
create index invites_organisation on app.invites (organisation_id, created_at);

create function app.invites_guard() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    new.email := lower(new.email);
    if current_user = 'app_api' then
      new.status := 'pending';
      new.invited_by := app.current_person_id();
      new.created_at := now();
      new.expires_at := now() + interval '7 days';
      new.accepted_at := null;
      new.accepted_by := null;
      new.cancelled_at := null;
      if new.role = 'owner' and not exists (
           select 1 from app.memberships m where m.organisation_id = new.organisation_id
             and m.person_id = app.current_person_id() and m.ended_at is null and m.role = 'owner') then
        raise exception using errcode = '42501', message = 'only an owner invites an owner';
      end if;
    end if;
    return new;
  end if;
  if new.status is distinct from old.status then
    if old.status <> 'pending' or new.status not in ('accepted', 'cancelled', 'expired') then
      perform app.refuse('an invite is pending, then accepted, cancelled or expired, and stays so');
    end if;
    if new.status = 'cancelled' then
      new.cancelled_at := now();
    end if;
  elsif old.status <> 'pending' and (to_jsonb(new) - 'expires_at') is distinct from (to_jsonb(old) - 'expires_at') then
    perform app.refuse('a closed invite does not change');
  end if;
  return new;
end
$$;

create trigger invites_guard before insert or update on app.invites
  for each row execute function app.invites_guard();

alter table app.invites enable row level security;
revoke all on app.invites from app_api, app_worker;
grant select, insert on app.invites to app_api;
grant update (status) on app.invites to app_api;
create policy invites_admin_view on app.invites for select to app_api
  using (app.is_org_admin(organisation_id));
create policy invites_admin_send on app.invites for insert to app_api
  with check (app.is_org_admin(organisation_id));
create policy invites_admin_cancel on app.invites for update to app_api
  using (app.is_org_admin(organisation_id)) with check (app.is_org_admin(organisation_id) and status = 'cancelled');

create function app.accept_invite(p_token_hash text)
  returns table (organisation_id uuid, role text, membership_id uuid)
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  me uuid := app.current_person_id();
  my_email text;
  inv app.invites;
  mid uuid;
begin
  select p.email into my_email from app.people p where p.id = me and p.closed_at is null;
  select * into inv from app.invites i where i.token_hash = p_token_hash for update;
  if my_email is null or inv.id is null or lower(inv.email) <> lower(my_email) then
    perform app.refuse('invite_not_found');
  end if;
  if inv.status <> 'pending'
     or exists (select 1 from app.organisations o where o.id = inv.organisation_id and o.closed_at is not null) then
    perform app.refuse('invite_closed');
  end if;
  if inv.expires_at <= now() then
    perform app.refuse('invite_expired');
  end if;
  if exists (select 1 from app.memberships m
             where m.organisation_id = inv.organisation_id and m.person_id = me and m.ended_at is null) then
    perform app.refuse('already_member');
  end if;
  insert into app.memberships (organisation_id, person_id, role) values (inv.organisation_id, me, inv.role)
    returning id into mid;
  update app.invites i set status = 'accepted', accepted_at = now(), accepted_by = me where i.id = inv.id;
  insert into app.audit_log (person_id, organisation_id, action, target)
  values (me, inv.organisation_id, 'invite.accepted', 'invite:' || inv.id::text);
  organisation_id := inv.organisation_id;
  role := inv.role;
  membership_id := mid;
  return next;
end
$$;

revoke execute on function app.invites_guard(), app.accept_invite(text) from public;
grant execute on function app.invites_guard(), app.accept_invite(text) to app_api;

reset role;
